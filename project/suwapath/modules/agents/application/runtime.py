"""Agent runtime (A01 base): context, runner, delivery and the event-driven supervisor.

Agents never call each other. They return message drafts; the runner stores them (deduplicated),
delivery verifies and routes them, and the supervisor turns events into jobs for the next agent.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol
from uuid import UUID

from suwapath.contracts.agents import AgentJob, AgentMessage, AgentResult, AgentSpec, MessageIntent
from suwapath.contracts.cases import CaseState
from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import (
    AgentKind,
    DataCategory,
    Language,
    MessageStatus,
    PrincipalKind,
    Purpose,
    Role,
    VerdictKind,
)
from suwapath.contracts.events import EventEnvelope
from suwapath.contracts.events.catalog import (
    AgentMessageSent,
    DoseMissed,
    PatientMessageReceived,
    TimerFired,
)
from suwapath.contracts.ids import CaseId, JobId, MessageId, PatientId, TraceId, UserId
from suwapath.contracts.ports.cases import CaseService
from suwapath.contracts.ports.consent import AccessDecision, AccessPolicy, AccessRequest
from suwapath.contracts.ports.notifications import NotificationDraft, NotificationService
from suwapath.contracts.ports.verifier import VerificationContext, Verifier
from suwapath.contracts.principal import Principal
from suwapath.modules.agents.application import messages
from suwapath.platform.clock import Clock
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.ids import new_id
from suwapath.platform.jobs import queue
from suwapath.platform.tracing import set_trace_id
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

logger = logging.getLogger("suwapath.agents")
MAX_JOB_ATTEMPTS = 3


class Directory(Protocol):
    """Who is who; bound to identity, consent and cases once those modules exist."""

    def patient_user(self, patient_id: PatientId) -> UserId: ...
    def guardians(self, patient_id: PatientId) -> list[UserId]: ...
    def case_patient(self, case_id: CaseId) -> PatientId: ...
    def language(self, patient_id: PatientId) -> Language: ...


@dataclass
class Dependencies:
    cases: CaseService
    policy: AccessPolicy
    verifier: Verifier
    notifications: NotificationService
    directory: Directory
    missed_doses: Callable[[UnitOfWork, PatientId, timedelta], Awaitable[int]]
    latest_patient_text: Callable[[CaseId], Awaitable[UntrustedText | None]]


class Agent(Protocol):
    spec: AgentSpec

    async def handle(self, job: AgentJob, ctx: AgentContext) -> AgentResult: ...


@dataclass
class AgentContext:
    """What an agent may use during one job. Reads go through ports with the agent's principal."""

    principal: Principal
    clock: Clock
    uow: UnitOfWork
    deps: Dependencies
    case_id: CaseId | None

    async def open_case(self, patient_id: PatientId, reason: str) -> CaseState:
        state = await self.deps.cases.open(
            self.principal,
            patient_id,
            UntrustedText(value=reason, source="system"),
            self.deps.directory.language(patient_id),
        )
        self.case_id = state.id
        return state

    async def missed_doses(self, patient_id: PatientId, window: timedelta) -> int:
        decision = await self.deps.policy.decide(
            AccessRequest(
                principal=self.principal,
                action="read",
                patient_id=patient_id,
                category=DataCategory.ADHERENCE,
                resource_type="MedicationAdministration",
                purpose=self.principal.purpose,
            )
        )
        if not decision.allowed:
            return 0
        return await self.deps.missed_doses(self.uow, patient_id, window)

    async def message(self, message_id: MessageId) -> AgentMessage | None:
        return await messages.load(self.uow, message_id)

    async def pending_request(self, intent: MessageIntent) -> AgentMessage | None:
        return (
            await messages.pending_request(self.uow, self.case_id, intent) if self.case_id else None
        )

    async def has_answer(self, request_id: MessageId) -> bool:
        return await messages.has_answer(self.uow, request_id)


def agent_principal(job: AgentJob, spec: AgentSpec, patient_scope: PatientId | None) -> Principal:
    return Principal(
        kind=PrincipalKind.AGENT,
        agent_kind=job.agent_kind,
        role=spec.acts_for_role,
        acting_for=job.acting_for,
        patient_scope=patient_scope,
        purpose=job.purpose,
        trace_id=job.trace_id,
    )


class AgentRunner:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, agents: dict[AgentKind, Agent], deps: Dependencies
    ) -> None:
        self.uow_factory = uow_factory
        self.agents = agents
        self.deps = deps

    async def run_once(self, worker_id: str) -> bool:
        job = await queue.claim(self.uow_factory, worker_id, set(self.agents))
        if job is None:
            return False
        set_trace_id(job.trace_id)
        agent = self.agents[job.agent_kind]
        try:
            async with self.uow_factory() as uow:
                patient = job.patient_id or (
                    self.deps.directory.case_patient(job.case_id) if job.case_id else None
                )
                ctx = AgentContext(
                    agent_principal(job, agent.spec, patient),
                    uow.clock,
                    uow,
                    self.deps,
                    job.case_id,
                )
                result = await agent.handle(job, ctx)
                case_id = ctx.case_id
                for draft in result.messages:
                    if case_id is None:
                        raise RuntimeError("an agent message needs a case")
                    key = (
                        draft.dedupe_hint
                        or f"{job.trigger_event_id or job.id}:{draft.intent.value}:"
                        f"{draft.recipient}"
                    )
                    await messages.send(
                        uow,
                        draft,
                        sender=job.agent_kind,
                        acting_for=job.acting_for,
                        case_id=case_id,
                        dedupe_key=key,
                        trace_id=job.trace_id,
                        now=uow.clock.now(),
                    )
                status = {
                    "done": "done",
                    "waiting": "done",
                    "handed_to_human": "done",
                    "failed": "failed",
                }[result.status]
                await queue.finish(uow, job.id, status)  # type: ignore[arg-type]
                await uow.commit()
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
            async with self.uow_factory() as uow:
                retry = job.attempts < MAX_JOB_ATTEMPTS
                await queue.finish(
                    uow,
                    job.id,
                    "queued" if retry else "failed",
                    error_code=type(exc).__name__[:80],
                    retry_at=uow.clock.now() + timedelta(seconds=2**job.attempts),
                )
                await uow.commit()
        return True


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
    def __init__(self, deps: Dependencies) -> None:
        self.deps = deps

    async def on_message_sent(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, AgentMessageSent)
        message = await messages.load(uow, payload.message_id)
        if message is None or message.status is not MessageStatus.DRAFTED:
            return
        if not message.needs_verification:
            await self._route(uow, message, envelope)
            return
        access = await self._recipient_access(message)
        verdict = await self.deps.verifier.check(
            message, VerificationContext(recipient_access=access)
        )
        if verdict.verdict is VerdictKind.ALLOW:
            await self._route(uow, message, envelope)
        elif verdict.verdict is VerdictKind.ESCALATE:
            if await messages.mark(
                uow, message.message_id, MessageStatus.ESCALATED, (MessageStatus.DRAFTED,)
            ):
                patient = self.deps.directory.case_patient(message.case_id)
                await self.deps.notifications.notify(
                    NotificationDraft(
                        user_id=self.deps.directory.patient_user(patient),
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
        if reviewer.patient_scope != self.deps.directory.case_patient(message.case_id):
            return False
        return await self._deliver_to_person(uow, message, (MessageStatus.ESCALATED,))

    async def _recipient_access(self, message: AgentMessage) -> AccessDecision:
        patient = self.deps.directory.case_patient(message.case_id)
        principal = self._recipient_principal(message, patient)
        for category in message.consent_scope:
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

    def _recipient_principal(self, message: AgentMessage, patient: PatientId) -> Principal:
        trace = TraceId(message.trace_id)
        if message.recipient == Role.GUARDIAN.value:
            return Principal(
                kind=PrincipalKind.USER,
                user_id=message.acting_for,
                role=Role.GUARDIAN,
                acting_for=message.acting_for,
                purpose=Purpose.GUARDIAN_MONITORING,
                trace_id=trace,
            )
        if message.recipient == AgentKind.PATIENT_COMPANION.value:
            return Principal(
                kind=PrincipalKind.AGENT,
                agent_kind=AgentKind.PATIENT_COMPANION,
                role=Role.PATIENT,
                acting_for=self.deps.directory.patient_user(patient),
                patient_scope=patient,
                purpose=Purpose.CARE_NAVIGATION,
                trace_id=trace,
            )
        return Principal(
            kind=PrincipalKind.AGENT,
            agent_kind=AgentKind(message.recipient),
            role=Role.GUARDIAN,
            acting_for=message.acting_for,
            patient_scope=patient,
            purpose=Purpose.GUARDIAN_MONITORING,
            trace_id=trace,
        )

    async def _route(self, uow: UnitOfWork, message: AgentMessage, envelope: EventEnvelope) -> None:
        if message.recipient in {k.value for k in AgentKind}:
            if not await messages.mark(
                uow, message.message_id, MessageStatus.DELIVERED, (MessageStatus.DRAFTED,)
            ):
                return
            patient = self.deps.directory.case_patient(message.case_id)
            recipient = AgentKind(message.recipient)
            acting_for = await self._recipient_human(uow, message, patient)
            enqueue(
                uow,
                kind=recipient,
                acting_for=acting_for,
                purpose=Purpose.CARE_NAVIGATION
                if recipient is AgentKind.PATIENT_COMPANION
                else Purpose.GUARDIAN_MONITORING,
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
            return self.deps.directory.patient_user(patient)
        parent = await messages.load(uow, message.in_reply_to) if message.in_reply_to else None
        return parent.acting_for if parent else None

    async def _deliver_to_person(
        self, uow: UnitOfWork, message: AgentMessage, only_from: tuple[MessageStatus, ...]
    ) -> bool:
        access = await self._recipient_access(
            message
        )  # SEC-13: consent is re-checked at delivery time
        if not access.allowed:
            await messages.mark(uow, message.message_id, MessageStatus.BLOCKED, only_from)
            return False
        if not await messages.mark(uow, message.message_id, MessageStatus.DELIVERED, only_from):
            return False
        assert message.acting_for is not None
        await self.deps.notifications.notify(
            NotificationDraft(
                user_id=message.acting_for,
                kind=message.intent.value,
                template_key=f"{message.intent.value}.v1",
                params=message.body,
                urgency="urgent" if message.body.get("level") == "urgent" else "normal",
            )
        )
        return True


# ----- Supervisor: events become agent jobs (EVT-13) --------------------------------------------


def register_supervisor(
    subscribers: SubscriberRegistry, deps: Dependencies, delivery: Delivery
) -> None:
    async def on_dose_missed(envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, DoseMissed)
        for guardian in deps.directory.guardians(payload.patient_id):
            enqueue(
                uow,
                kind=AgentKind.GUARDIAN_AGENT,
                acting_for=guardian,
                purpose=Purpose.GUARDIAN_MONITORING,
                case_id=None,
                patient_id=payload.patient_id,
                trigger=envelope,
                data={"trigger": "dose_missed", "patient_id": str(payload.patient_id)},
            )

    async def on_patient_message(envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, PatientMessageReceived)
        patient = deps.directory.case_patient(payload.case_id)
        enqueue(
            uow,
            kind=AgentKind.PATIENT_COMPANION,
            acting_for=deps.directory.patient_user(patient),
            purpose=Purpose.CARE_NAVIGATION,
            case_id=payload.case_id,
            patient_id=patient,
            trigger=envelope,
            data={"trigger": "patient_message"},
        )

    async def on_timer(envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, TimerFired)
        if payload.kind != messages.DEADLINE_TIMER:
            return
        request = await messages.load(uow, MessageId(UUID(payload.ref_id)))
        if request is None or await messages.has_answer(uow, request.message_id):
            return
        patient = deps.directory.case_patient(request.case_id)
        enqueue(
            uow,
            kind=request.sender,
            acting_for=request.acting_for,
            purpose=Purpose.GUARDIAN_MONITORING
            if request.sender is AgentKind.GUARDIAN_AGENT
            else Purpose.CARE_NAVIGATION,
            case_id=request.case_id,
            patient_id=patient,
            trigger=envelope,
            data={"timeout_of": str(request.message_id)},
        )

    subscribers.add("care.dose_missed", on_dose_missed, name="agents.guardian_on_dose_missed")
    subscribers.add(
        "cases.patient_message_received",
        on_patient_message,
        name="agents.companion_on_patient_message",
    )
    subscribers.add("platform.timer_fired", on_timer, name="agents.request_deadline")
    subscribers.add("agents.message_sent", delivery.on_message_sent, name="agents.delivery")
