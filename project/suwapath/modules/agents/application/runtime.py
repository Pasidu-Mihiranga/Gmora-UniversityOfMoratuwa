"""Agent runtime (A01): registry, runner with limits, delivery and the event-driven supervisor.

Agents never call each other. They return message drafts and case events; the runner stores them
in the job's transaction (messages deduplicated, case events with the version the agent saw),
delivery verifies and routes messages, and the supervisor turns events into jobs (EVT-13).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any, Protocol, get_args
from uuid import UUID

from sqlalchemy import select

from suwapath.contracts.agents import (
    AgentJob,
    AgentMessage,
    AgentResult,
    AgentSpec,
    MessageIntent,
)
from suwapath.contracts.cases import CaseEventDraft, CaseEventKind
from suwapath.contracts.enums import (
    AgentKind,
    DataCategory,
    MessageStatus,
    PrincipalKind,
    Purpose,
    Role,
    VerdictKind,
)
from suwapath.contracts.errors import InvalidTransition, NotFound, VersionConflict
from suwapath.contracts.events import EventEnvelope
from suwapath.contracts.events.catalog import (
    AgentMessageSent,
    AlertRuleFired,
    AppointmentBooked,
    CaseOpened,
    DoctorDecisionRecorded,
    DoseMissed,
    HandedToHuman,
    PatientMessageReceived,
    RedFlagRaised,
    ReportExtracted,
    ResourceWritten,
    ScreeningCompleted,
    TimerFired,
    VerdictIssued,
)
from suwapath.contracts.ids import CaseId, JobId, MessageId, PatientId, TraceId, UserId
from suwapath.contracts.ports.consent import AccessDecision, AccessRequest
from suwapath.contracts.ports.notifications import NotificationDraft
from suwapath.contracts.ports.verifier import VerificationContext
from suwapath.contracts.principal import Principal
from suwapath.modules.agents.application import messages
from suwapath.modules.agents.application.context import (
    AgentContext,
    Dependencies,
    Directory,
    JobLimit,
    Messenger,
    ScopedCaseReader,
    ScopedModelClient,
    StepCounter,
)
from suwapath.modules.agents.application.tools import ToolInvoker
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.gateway.gateway import ModelGateway
from suwapath.platform.ids import new_id
from suwapath.platform.jobs import queue
from suwapath.platform.models import TimerRow
from suwapath.platform.tools import ToolRegistry
from suwapath.platform.tracing import set_trace_id
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

__all__ = [
    "AgentContext",
    "AgentRegistry",
    "AgentRunner",
    "Delivery",
    "Dependencies",
    "Directory",
    "enqueue",
    "register_supervisor",
]

logger = logging.getLogger("suwapath.agents")
MAX_JOB_ATTEMPTS = 3
LEASE_RENEW_SECONDS = 30.0  # EVT-11
CHECKIN_FOLLOWUP_TIMER = "guardian_checkin_followup"
HANDOVER_REASONS = frozenset(get_args(HandedToHuman.model_fields["reason"].annotation))
PURPOSE_OF: dict[AgentKind, Purpose] = {
    AgentKind.PATIENT_COMPANION: Purpose.CARE_NAVIGATION,
    AgentKind.GUARDIAN_AGENT: Purpose.GUARDIAN_MONITORING,
    AgentKind.DOCTOR_ASSISTANT: Purpose.DOCTOR_BRIEF,
    AgentKind.SINGLE_AGENT_BASELINE: Purpose.EVALUATION,
}
RECIPIENT_ROLE: dict[AgentKind, Role] = {
    AgentKind.PATIENT_COMPANION: Role.PATIENT,
    AgentKind.GUARDIAN_AGENT: Role.GUARDIAN,
    AgentKind.DOCTOR_ASSISTANT: Role.DOCTOR,
}
PERSON_PURPOSE: dict[str, Purpose] = {
    Role.GUARDIAN.value: Purpose.GUARDIAN_MONITORING,
    Role.DOCTOR.value: Purpose.TREATMENT,
}
DepsSource = Dependencies | Callable[[], Dependencies]


class Agent(Protocol):
    spec: AgentSpec

    async def handle(self, job: AgentJob, ctx: AgentContext) -> AgentResult: ...


class AgentRegistry:
    """AGT-1: the runtime looks agents up by `job.agent_kind`."""

    def __init__(self, agents: Iterable[Agent] = ()) -> None:
        self._agents: dict[AgentKind, Agent] = {}
        for agent in agents:
            self.add(agent)

    def add(self, agent: Agent) -> None:
        if agent.spec.kind in self._agents:
            raise ValueError(f"agent {agent.spec.kind} registered twice")
        self._agents[agent.spec.kind] = agent

    def get(self, kind: AgentKind) -> Agent | None:
        return self._agents.get(kind)

    def kinds(self) -> set[AgentKind]:
        return set(self._agents)


def _resolve(deps: DepsSource) -> Dependencies:
    return deps if isinstance(deps, Dependencies) else deps()


def agent_principal(job: AgentJob, spec: AgentSpec, patient_scope: PatientId | None) -> Principal:
    """CON-6: built only here, from a job whose origin was validated when it was enqueued."""
    return Principal(
        kind=PrincipalKind.AGENT,
        agent_kind=job.agent_kind,
        role=spec.acts_for_role,
        acting_for=job.acting_for,
        patient_scope=patient_scope,
        purpose=job.purpose,
        trace_id=job.trace_id,
    )


# ----- Runner ------------------------------------------------------------------------------------


class AgentRunner:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        agents: AgentRegistry | dict[AgentKind, Agent],
        deps: DepsSource,
        *,
        tools: ToolRegistry | None = None,
        gateway: ModelGateway | Callable[[], ModelGateway | None] | None = None,
        checkpointer: Any = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.agents = (
            agents if isinstance(agents, AgentRegistry) else AgentRegistry(agents.values())
        )
        self._deps = deps
        self.tools = tools or ToolRegistry()
        self._gateway = gateway
        self.checkpointer = checkpointer

    @property
    def deps(self) -> Dependencies:
        return _resolve(self._deps)

    def _model_gateway(self) -> ModelGateway | None:
        if self._gateway is None or isinstance(self._gateway, ModelGateway):
            return self._gateway
        return self._gateway()

    async def run_once(self, worker_id: str) -> bool:
        """Claim and run one job. False when nothing was due."""
        job = await queue.claim(self.uow_factory, worker_id, self.agents.kinds())
        if job is None:
            return False
        set_trace_id(job.trace_id)
        agent = self.agents.get(job.agent_kind)
        assert agent is not None  # claim only returns registered kinds
        renewal = asyncio.create_task(self._renew(job.id, worker_id))
        try:
            await self._run(job, agent)
        except JobLimit as limit:
            await self._hand_over(job, agent, "agent_failure", limit.code)
        except Exception as exc:
            logger.warning(
                "agent job failed",
                extra={
                    "agent_kind": job.agent_kind.value,
                    "job_id": str(job.id),
                    "attempt": job.attempts,
                    "error_code": type(exc).__name__,
                },
            )
            if job.attempts < MAX_JOB_ATTEMPTS:
                async with self.uow_factory() as uow:
                    await queue.finish(
                        uow,
                        job.id,
                        "queued",
                        error_code=type(exc).__name__[:80],
                        retry_at=uow.clock.now() + timedelta(seconds=2**job.attempts),
                    )
                    await uow.commit()
            else:
                await self._hand_over(job, agent, "agent_failure", type(exc).__name__[:80])
        finally:
            renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewal
        return True

    async def _renew(self, job_id: JobId, worker_id: str) -> None:
        while True:
            await asyncio.sleep(LEASE_RENEW_SECONDS)
            await queue.renew_lease(self.uow_factory, job_id, worker_id)

    async def _run(self, job: AgentJob, agent: Agent) -> None:
        deps = self.deps
        async with self.uow_factory() as uow:
            ctx = await self._context(job, agent, uow, deps)
            try:
                result = await asyncio.wait_for(
                    agent.handle(job, ctx), timeout=agent.spec.max_wall_seconds
                )
            except TimeoutError:
                raise JobLimit("max_wall_seconds") from None
            for draft in result.messages:
                await ctx.messenger.send(draft)
            if result.case_events:
                await self._append(ctx, list(result.case_events))
            if result.status == "handed_to_human":
                reason = (
                    result.reason
                    if result.reason is not None and result.reason in HANDOVER_REASONS
                    else "agent_failure"
                )
                await self._append_handover(ctx, reason)
            await queue.finish(
                uow,
                job.id,
                "failed" if result.status == "failed" else "done",
                error_code=result.reason[:80] if result.reason else None,
            )
            await uow.commit()

    async def _context(
        self, job: AgentJob, agent: Agent, uow: UnitOfWork, deps: Dependencies
    ) -> AgentContext:
        patient = job.patient_id
        if patient is None and job.case_id is not None:
            patient = await deps.directory.case_patient(job.case_id)
        principal = agent_principal(job, agent.spec, patient)
        steps = StepCounter(agent.spec.max_steps)
        cases = ScopedCaseReader(deps.cases, principal)
        cases.case_id = job.case_id
        return AgentContext(
            principal=principal,
            clock=uow.clock,
            uow=uow,
            deps=deps,
            spec=agent.spec,
            job=job,
            cases=cases,
            tools=ToolInvoker(self.tools, agent.spec, principal, uow, steps),
            model=ScopedModelClient(self._model_gateway(), agent.spec, principal, cases, steps),
            messenger=Messenger(uow, agent.spec, job, cases),
            steps=steps,
            checkpointer=self.checkpointer,
        )

    async def _append(self, ctx: AgentContext, drafts: list[CaseEventDraft]) -> None:
        """EVT-20: with the version the agent decided on. A conflict fails the job, which then
        runs again on fresh state (at most MAX_JOB_ATTEMPTS times)."""
        if ctx.case_id is None:
            raise NotFound()
        expected = ctx.cases.seen_version
        if expected is None:
            expected = (await ctx.cases.get()).version
        state = await ctx.deps.cases.append(ctx.principal, ctx.case_id, drafts, expected)
        ctx.cases.seen_version = state.version

    async def _append_handover(self, ctx: AgentContext, reason: str) -> None:
        if ctx.case_id is None:
            return
        try:
            state = await ctx.cases.get()
            await ctx.deps.cases.append(
                ctx.principal,
                ctx.case_id,
                [
                    CaseEventDraft(
                        kind=CaseEventKind.HANDED_TO_HUMAN,
                        data={"reason": reason},
                        actor=ctx.spec.kind.value,
                    )
                ],
                state.version,
            )
        except (InvalidTransition, NotFound, VersionConflict):
            logger.warning(
                "hand over not recorded on the case",
                extra={"case_id": str(ctx.case_id), "agent_kind": ctx.spec.kind.value},
            )

    async def _hand_over(self, job: AgentJob, agent: Agent, reason: str, code: str) -> None:
        """A limit or the last failed attempt: discard the job's work, tell the case (AGT-3)."""
        logger.warning(
            "agent job handed to a human",
            extra={"agent_kind": job.agent_kind.value, "job_id": str(job.id), "error_code": code},
        )
        async with self.uow_factory() as uow:
            ctx = await self._context(job, agent, uow, self.deps)
            await self._append_handover(ctx, reason)
            await queue.finish(uow, job.id, "failed", error_code=code[:80])
            await uow.commit()


def enqueue(
    uow: UnitOfWork,
    *,
    kind: AgentKind,
    acting_for: UserId | None,
    purpose: Purpose,
    case_id: CaseId | None,
    patient_id: PatientId | None,
    trigger: EventEnvelope,
    data: dict[str, Any],
) -> JobId:
    job = AgentJob(
        id=JobId(new_id()),
        agent_kind=kind,
        case_id=case_id,
        patient_id=patient_id,
        trigger_event_id=trigger.event_id,
        acting_for=acting_for,
        purpose=purpose,
        input=data,
        trace_id=trigger.trace_id,
    )
    return queue.enqueue(uow, job)


# ----- Delivery: verify, then route to an agent or a person (AGT-10, SEC-13) --------------------


class Delivery:
    def __init__(self, deps: DepsSource) -> None:
        self._deps = deps

    @property
    def deps(self) -> Dependencies:
        return _resolve(self._deps)

    async def on_message_sent(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, AgentMessageSent)
        message = await messages.load(uow, payload.message_id)
        if message is None or message.status is not MessageStatus.DRAFTED:
            return
        if message.intent is MessageIntent.REPLY_DRAFT:
            return  # AGT-30: only the doctor's approval flow can deliver a reply draft
        if not message.needs_verification:
            await self._route(uow, message, envelope)
            return
        access = await self._recipient_access(uow, message)
        verdict = await self.deps.verifier.check(
            message, VerificationContext(recipient_access=access)
        )
        if verdict.verdict is VerdictKind.ALLOW:
            await self._route(uow, message, envelope)
        elif verdict.verdict is VerdictKind.ESCALATE:
            if await messages.mark(
                uow, message.message_id, MessageStatus.ESCALATED, (MessageStatus.DRAFTED,)
            ):
                patient = await self.deps.directory.case_patient(message.case_id)
                await self.deps.notifications.notify(
                    NotificationDraft(
                        user_id=await self.deps.directory.patient_user(patient),
                        kind="review_required",
                        template_key=f"review.{message.intent.value}",
                        params={"message_id": str(message.message_id)},
                    )
                )
        else:
            status = (
                MessageStatus.RETURNED
                if verdict.verdict is VerdictKind.REVISE
                else MessageStatus.BLOCKED
            )
            await messages.mark(uow, message.message_id, status, (MessageStatus.DRAFTED,))

    async def approve_escalated(
        self, uow: UnitOfWork, reviewer: Principal, message_id: MessageId
    ) -> bool:
        """VER-5: the patient approves a guardian-bound message held for review."""
        message = await messages.load(uow, message_id)
        if message is None or message.status is not MessageStatus.ESCALATED:
            return False
        if reviewer.patient_scope != await self.deps.directory.case_patient(message.case_id):
            return False
        return await self._deliver_to_person(uow, message, (MessageStatus.ESCALATED,))

    async def _recipient_access(self, uow: UnitOfWork, message: AgentMessage) -> AccessDecision:
        """Allowed when at least one receiving person (or the receiving agent) may see it."""
        patient = await self.deps.directory.case_patient(message.case_id)
        if message.recipient in PERSON_PURPOSE:
            targets = await self._allowed_people(message, patient)
            if targets:
                return AccessDecision(
                    allowed=True, reason="recipient_allowed", policy_version="runtime"
                )
            return AccessDecision(
                allowed=False, reason="no_recipient_allowed", policy_version="runtime"
            )
        principal = await self._recipient_principal(uow, message, patient)
        return await self._decide(principal, patient, message.consent_scope)

    async def _decide(
        self, principal: Principal, patient: PatientId, scope: frozenset[DataCategory]
    ) -> AccessDecision:
        for category in sorted(scope):
            decision = await self.deps.policy.decide(
                AccessRequest(
                    principal=principal,
                    action="read",
                    patient_id=patient,
                    category=category,
                    resource_type="AgentMessage",
                    purpose=principal.purpose,
                )
            )
            if not decision.allowed:
                return decision
        return AccessDecision(
            allowed=True, reason="all_categories_allowed", policy_version="runtime"
        )

    async def _allowed_people(self, message: AgentMessage, patient: PatientId) -> list[UserId]:
        """Person recipients, each checked for themselves. The emergency alert goes to every
        guardian under the emergency purpose (SEC-10 rule 7); other messages go to the person the
        sending agent acts for (the guardian for an alert, the doctor for a brief)."""
        role = Role(message.recipient)
        if message.intent is MessageIntent.EMERGENCY_ALERT:
            people = await self.deps.directory.guardians(patient)
            purpose = Purpose.EMERGENCY
        else:
            people = [message.acting_for] if message.acting_for else []
            purpose = PERSON_PURPOSE[message.recipient]
        allowed = []
        for person in people:
            principal = Principal(
                kind=PrincipalKind.USER,
                user_id=person,
                role=role,
                acting_for=person,
                purpose=purpose,
                trace_id=TraceId(message.trace_id),
            )
            if (await self._decide(principal, patient, message.consent_scope)).allowed:
                allowed.append(person)
        return allowed

    async def _recipient_principal(
        self, uow: UnitOfWork, message: AgentMessage, patient: PatientId
    ) -> Principal:
        """Who receives the message, acting for whom: consent is checked for that person."""
        trace = TraceId(message.trace_id)
        recipient = AgentKind(message.recipient)
        return Principal(
            kind=PrincipalKind.AGENT,
            agent_kind=recipient,
            role=RECIPIENT_ROLE.get(recipient),
            # the human the receiving agent acts for, never the sender's person
            acting_for=await self._recipient_human(uow, message, patient),
            patient_scope=patient,
            purpose=PURPOSE_OF.get(recipient, Purpose.CARE_NAVIGATION),
            trace_id=trace,
        )

    async def _route(self, uow: UnitOfWork, message: AgentMessage, envelope: EventEnvelope) -> None:
        if message.recipient in {k.value for k in AgentKind}:
            if not await messages.mark(
                uow, message.message_id, MessageStatus.DELIVERED, (MessageStatus.DRAFTED,)
            ):
                return
            patient = await self.deps.directory.case_patient(message.case_id)
            recipient = AgentKind(message.recipient)
            acting_for = await self._recipient_human(uow, message, patient)
            enqueue(
                uow,
                kind=recipient,
                acting_for=acting_for,
                purpose=PURPOSE_OF.get(recipient, Purpose.CARE_NAVIGATION),
                case_id=message.case_id,
                patient_id=patient,
                trigger=envelope,
                data={"message_id": str(message.message_id)},
            )
        else:
            await self._deliver_to_person(uow, message, (MessageStatus.DRAFTED,))

    async def _recipient_human(
        self, uow: UnitOfWork, message: AgentMessage, patient: PatientId
    ) -> UserId | None:
        if message.recipient == AgentKind.PATIENT_COMPANION.value:
            return await self.deps.directory.patient_user(patient)
        parent = await messages.load(uow, message.in_reply_to) if message.in_reply_to else None
        return parent.acting_for if parent else None

    async def _deliver_to_person(
        self, uow: UnitOfWork, message: AgentMessage, only_from: tuple[MessageStatus, ...]
    ) -> bool:
        patient = await self.deps.directory.case_patient(message.case_id)
        people = await self._allowed_people(message, patient)  # SEC-13: checked at delivery
        if not people:
            await messages.mark(uow, message.message_id, MessageStatus.BLOCKED, only_from)
            return False
        if not await messages.mark(uow, message.message_id, MessageStatus.DELIVERED, only_from):
            return False
        urgent = (
            message.intent is MessageIntent.EMERGENCY_ALERT or message.body.get("level") == "urgent"
        )
        for person in people:
            await self.deps.notifications.notify(
                NotificationDraft(
                    user_id=person,
                    kind=message.intent.value,
                    template_key=f"{message.intent.value}.v1",
                    params=message.body,
                    urgency="urgent" if urgent else "normal",
                )
            )
        return True


# ----- Supervisor: events become agent jobs (EVT-13) --------------------------------------------


class Supervisor:
    """One subscriber per row of the EVT-13 table. Jobs are created only for registered agents,
    so an event for an agent that is not deployed is consumed without leaving a stuck job."""

    def __init__(self, deps: DepsSource, kinds: set[AgentKind] | None = None) -> None:
        self._deps = deps
        self._kinds = kinds

    @property
    def deps(self) -> Dependencies:
        return _resolve(self._deps)

    def _enqueue(
        self,
        uow: UnitOfWork,
        envelope: EventEnvelope,
        kind: AgentKind,
        *,
        acting_for: UserId | None,
        case_id: CaseId | None,
        patient_id: PatientId | None,
        data: dict[str, Any],
        purpose: Purpose | None = None,
    ) -> None:
        if self._kinds is not None and kind not in self._kinds:
            return
        enqueue(
            uow,
            kind=kind,
            acting_for=acting_for,
            purpose=purpose or PURPOSE_OF[kind],
            case_id=case_id,
            patient_id=patient_id,
            trigger=envelope,
            data=data,
        )

    async def on_case_opened(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, CaseOpened)
        if payload.opened_by != "patient":
            return  # cases opened by an agent already have their agent at work
        self._enqueue(
            uow,
            envelope,
            AgentKind.PATIENT_COMPANION,
            acting_for=await self.deps.directory.patient_user(payload.patient_id),
            case_id=payload.case_id,
            patient_id=payload.patient_id,
            data={"trigger": "case_opened"},
        )

    async def on_patient_message(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, PatientMessageReceived)
        patient = await self.deps.directory.case_patient(payload.case_id)
        self._enqueue(
            uow,
            envelope,
            AgentKind.PATIENT_COMPANION,
            acting_for=await self.deps.directory.patient_user(patient),
            case_id=payload.case_id,
            patient_id=patient,
            data={"trigger": "patient_message"},
        )

    async def on_dose_missed(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, DoseMissed)
        for guardian in await self.deps.directory.guardians(payload.patient_id):
            self._enqueue(
                uow,
                envelope,
                AgentKind.GUARDIAN_AGENT,
                acting_for=guardian,
                case_id=None,
                patient_id=payload.patient_id,
                data={"trigger": "dose_missed", "patient_id": str(payload.patient_id)},
            )

    async def on_red_flag(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, RedFlagRaised)
        for guardian in await self.deps.directory.guardians(payload.patient_id):
            self._enqueue(
                uow,
                envelope,
                AgentKind.GUARDIAN_AGENT,
                acting_for=guardian,
                case_id=payload.case_id,
                patient_id=payload.patient_id,
                data={"trigger": "red_flag", "rule_ids": list(payload.rule_ids)},
                purpose=Purpose.EMERGENCY,
            )

    async def on_alert_rule(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        """CARE-8: a guardian's rule triggers the guardian agent; it never messages directly."""
        payload = envelope.payload
        assert isinstance(payload, AlertRuleFired)
        if payload.rule_type == "red_flag":
            return  # red flags reach the guardian agent through cases.red_flag_raised
        guardians = (
            [payload.guardian_id]
            if payload.guardian_id
            else await self.deps.directory.guardians(payload.patient_id)
        )
        for guardian in guardians:
            self._enqueue(
                uow,
                envelope,
                AgentKind.GUARDIAN_AGENT,
                acting_for=guardian,
                case_id=None,
                patient_id=payload.patient_id,
                data={
                    "trigger": "alert_rule",
                    "rule_type": payload.rule_type,
                    "rule_id": str(payload.rule_id),
                },
            )

    async def on_appointment_booked(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, AppointmentBooked)
        if payload.case_id is None:
            return
        doctor = await self.deps.directory.practitioner_user(payload.practitioner_role_id)
        if doctor is None:
            return
        self._enqueue(
            uow,
            envelope,
            AgentKind.DOCTOR_ASSISTANT,
            acting_for=doctor,
            case_id=payload.case_id,
            patient_id=payload.patient_id,
            data={"trigger": "appointment_booked", "appointment_id": str(payload.appointment_id)},
        )

    async def on_new_clinical_data(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        """Reports, imaging and record writes reach the doctor assistant of a booked case."""
        payload = envelope.payload
        assert isinstance(payload, ReportExtracted | ScreeningCompleted | ResourceWritten)
        found = await self.deps.directory.doctor_case(payload.patient_id)
        if found is None:
            return
        case_id, doctor = found
        self._enqueue(
            uow,
            envelope,
            AgentKind.DOCTOR_ASSISTANT,
            acting_for=doctor,
            case_id=case_id,
            patient_id=payload.patient_id,
            data={"trigger": envelope.type},
        )

    async def on_doctor_decision(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, DoctorDecisionRecorded)
        draft = await messages.load(uow, payload.draft_id)
        if draft is None or draft.sender is not AgentKind.DOCTOR_ASSISTANT:
            return
        self._enqueue(
            uow,
            envelope,
            AgentKind.DOCTOR_ASSISTANT,
            acting_for=draft.acting_for,
            case_id=payload.case_id,
            patient_id=None,
            data={
                "trigger": "doctor_decision",
                "draft_id": str(payload.draft_id),
                "decision": payload.decision.value,
            },
        )

    async def on_verdict(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, VerdictIssued)
        if payload.verdict is not VerdictKind.REVISE:
            return
        message = await messages.load(uow, payload.message_id)
        if message is None or message.sender is AgentKind.VERIFIER:
            return
        self._enqueue(
            uow,
            envelope,
            message.sender,
            acting_for=message.acting_for,
            case_id=message.case_id,
            patient_id=None,
            data={"revise_of": str(message.message_id)},
        )

    async def on_timer(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, TimerFired)
        if payload.kind == messages.DEADLINE_TIMER:
            await self._request_deadline(envelope, uow, payload)
        elif payload.kind == CHECKIN_FOLLOWUP_TIMER:
            await self._checkin_followup(envelope, uow, payload)

    async def _request_deadline(
        self, envelope: EventEnvelope, uow: UnitOfWork, payload: TimerFired
    ) -> None:
        request = await messages.load(uow, MessageId(UUID(payload.ref_id)))
        if request is None or await messages.has_answer(uow, request.message_id):
            return
        self._enqueue(
            uow,
            envelope,
            request.sender,
            acting_for=request.acting_for,
            case_id=request.case_id,
            patient_id=await self.deps.directory.case_patient(request.case_id),
            data={"timeout_of": str(request.message_id)},
        )

    async def _checkin_followup(
        self, envelope: EventEnvelope, uow: UnitOfWork, payload: TimerFired
    ) -> None:
        """Timer payload: patient_id, guardian_id, case_id and request_id, set by the guardian
        agent when a check-in passed its deadline unanswered."""
        timer = (
            await uow.session.execute(
                select(TimerRow.payload).where(TimerRow.id == payload.timer_id)
            )
        ).scalar_one_or_none()
        if not timer:
            return
        self._enqueue(
            uow,
            envelope,
            AgentKind.GUARDIAN_AGENT,
            acting_for=UserId(UUID(timer["guardian_id"])),
            case_id=CaseId(UUID(timer["case_id"])) if timer.get("case_id") else None,
            patient_id=PatientId(UUID(timer["patient_id"])),
            data={"trigger": "checkin_followup", "request_id": timer.get("request_id")},
        )

    def register(self, subscribers: SubscriberRegistry) -> None:
        add = subscribers.add
        add("cases.case_opened", self.on_case_opened, name="agents.companion_on_case_opened")
        add(
            "cases.patient_message_received",
            self.on_patient_message,
            name="agents.companion_on_patient_message",
        )
        add("care.dose_missed", self.on_dose_missed, name="agents.guardian_on_dose_missed")
        add("cases.red_flag_raised", self.on_red_flag, name="agents.guardian_on_red_flag")
        add("care.alert_rule_fired", self.on_alert_rule, name="agents.guardian_on_alert_rule")
        add(
            "booking.appointment_booked",
            self.on_appointment_booked,
            name="agents.doctor_on_appointment_booked",
        )
        for event_type in (
            "reports.report_extracted",
            "imaging.screening_completed",
            "record.resource_written",
        ):
            add(
                event_type,
                self.on_new_clinical_data,
                name=f"agents.doctor_on_{event_type.split('.')[1]}",
            )
        add(
            "cases.doctor_decision_recorded",
            self.on_doctor_decision,
            name="agents.doctor_on_decision",
        )
        add("verifier.verdict_issued", self.on_verdict, name="agents.author_on_revise")
        add("platform.timer_fired", self.on_timer, name="agents.timers")


def register_supervisor(
    subscribers: SubscriberRegistry,
    deps: DepsSource,
    delivery: Delivery,
    kinds: set[AgentKind] | None = None,
) -> Supervisor:
    supervisor = Supervisor(deps, kinds)
    supervisor.register(subscribers)
    subscribers.add("agents.message_sent", delivery.on_message_sent, name="agents.delivery")
    return supervisor
