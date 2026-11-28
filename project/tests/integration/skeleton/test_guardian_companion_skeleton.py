"""Walking skeleton: missed doses -> guardian agent -> companion -> Amma -> verifier -> Nimal.

Real: Postgres outbox, inbox, timers, agent job queue, agent messages, care dose records, the agent
runtime, delivery and the rules verifier. In-memory doubles (one process): cases, consent data,
notifications. No model keys: the skeleton agents are deterministic (offline operation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy import text as sql

from suwapath.contracts.agents import MessageIntent
from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import (
    AgentKind,
    DataCategory,
    DoseSlot,
    Language,
    MessageStatus,
    PrincipalKind,
    Purpose,
    Role,
)
from suwapath.contracts.events.catalog import PatientMessageReceived
from suwapath.contracts.ids import CaseId, PatientId, ResourceId, TraceId, UserId
from suwapath.contracts.ports.consent import AccessRequest
from suwapath.contracts.principal import Principal
from suwapath.contracts.testing.doubles_clinical import InMemoryCaseService, KeywordSafetyService
from suwapath.contracts.testing.doubles_core import (
    InMemoryAccessPolicy,
    InMemoryNotificationService,
)
from suwapath.modules.agents.adapters.models import AgentMessageRow
from suwapath.modules.agents.application import runtime
from suwapath.modules.agents.application.skeleton_agents import CompanionAgent, GuardianAgent
from suwapath.modules.care.application import doses
from suwapath.modules.verifier.application.service import SqlVerifier
from suwapath.modules.verifier.domain.checks import default_checks
from suwapath.platform.clock import FixedClock
from suwapath.platform.events.dispatcher import Dispatcher
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.jobs.timers import fire_due_timers
from suwapath.platform.models import InboxRow, OutboxRow
from suwapath.platform.tracing import set_trace_id
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
TRACE = "a1b2c3d4" * 4


@dataclass
class World:
    amma: PatientId = field(default_factory=lambda: PatientId(uuid4()))
    amma_user: UserId = field(default_factory=lambda: UserId(uuid4()))
    nimal: UserId = field(default_factory=lambda: UserId(uuid4()))
    cases: InMemoryCaseService | None = None

    async def patient_user(self, patient_id: PatientId) -> UserId:
        return self.amma_user

    async def guardians(self, patient_id: PatientId) -> list[UserId]:
        return [self.nimal]

    async def case_patient(self, case_id: CaseId) -> PatientId:
        assert self.cases is not None
        return self.cases.states[case_id].patient_id

    async def language(self, patient_id: PatientId) -> Language:
        return Language.SI

    async def practitioner_user(self, practitioner_role_id: object) -> UserId | None:
        return None

    async def doctor_case(self, patient_id: PatientId) -> None:
        return None


@dataclass(frozen=True)
class Sent:
    id: UUID
    sender: str
    status: str
    trace_id: str
    case_id: UUID


@dataclass
class Harness:
    world: World
    clock: FixedClock
    factory: UnitOfWorkFactory
    policy: InMemoryAccessPolicy
    cases: InMemoryCaseService
    notifications: InMemoryNotificationService
    dispatcher: Dispatcher
    runner: runtime.AgentRunner
    delivery: runtime.Delivery
    verifier: SqlVerifier

    async def pump(self) -> None:
        """Run timers, event delivery and agent jobs until the system is quiet."""
        for _ in range(50):
            busy = await fire_due_timers(self.factory) + await self.dispatcher.drain()
            while await self.runner.run_once("skeleton-worker"):
                busy += 1
            if not busy:
                return
        raise AssertionError("system did not settle")

    async def schedule_missed_evenings(self) -> None:
        async with self.factory() as uow:
            for hours_ago in (30, 6):
                await doses.schedule_dose_check(
                    uow,
                    patient_id=self.world.amma,
                    statement_id=ResourceId(uuid4()),
                    scheduled_at=self.clock.now() - timedelta(hours=hours_ago),
                    slot=DoseSlot.EVENING,
                )
            await uow.commit()

    async def amma_replies(self, text: str) -> None:
        case_id = next(iter(self.cases.states))
        self.cases.communications.append((case_id, "patient", text))
        async with self.factory() as uow:
            uow.publish(
                PatientMessageReceived(case_id=case_id, communication_id=ResourceId(uuid4())),
                aggregate_id=str(case_id),
                correlation_id=case_id,
            )
            await uow.commit()

    async def messages(self, intent: MessageIntent | None = None) -> list[Sent]:
        async with self.factory() as uow:
            query = select(AgentMessageRow).order_by(AgentMessageRow.created_at)
            if intent:
                query = query.where(AgentMessageRow.intent == intent.value)
            rows = (await uow.session.execute(query)).scalars()
            return [Sent(r.id, r.sender, r.status, r.trace_id, r.case_id) for r in rows]

    async def approve_alert(self) -> bool:
        """Amma sends the alert from her review queue (VER-5); delivery acts on her verdict."""
        amma = Principal(
            kind=PrincipalKind.USER,
            user_id=self.world.amma_user,
            role=Role.PATIENT,
            acting_for=self.world.amma_user,
            patient_scope=self.world.amma,
            purpose=Purpose.SELF_ACCESS,
            trace_id=TraceId(TRACE),
        )
        page = await self.verifier.reviews(amma, None, 10)
        if len(page.items) != 1:
            return False
        async with self.factory() as uow:
            await self.verifier.decide_review(amma, page.items[0].id, "send", None)
            await uow.commit()
        await self.pump()
        alert = (await self.messages(MessageIntent.GUARDIAN_ALERT))[0]
        return alert.status == MessageStatus.DELIVERED.value

    def notifications_for(self, user: UserId) -> list[dict[str, object]]:
        return [{"kind": n.kind, **n.params} for n in self.notifications.items if n.user_id == user]


@pytest.fixture
def harness(uow_factory: UnitOfWorkFactory, clock: FixedClock) -> Harness:
    set_trace_id(TRACE)
    world = World()
    cases = InMemoryCaseService(now=clock.now)
    world.cases = cases
    policy = InMemoryAccessPolicy(
        patients_by_user={world.amma_user: world.amma},
        guardian_grants={
            (world.nimal, world.amma): frozenset(
                {DataCategory.ADHERENCE, DataCategory.APPOINTMENTS}
            )
        },
        agent_scopes={
            AgentKind.GUARDIAN_AGENT.value: GuardianAgent.spec.data_scope,
            AgentKind.PATIENT_COMPANION.value: CompanionAgent.spec.data_scope,
        },
        user_roles={world.nimal: Role.GUARDIAN, world.amma_user: Role.PATIENT},
    )
    notifications = InMemoryNotificationService(now=clock.now)
    verifier = SqlVerifier(
        uow_factory,
        lambda: default_checks(KeywordSafetyService().check_wording),
        lambda: {"agent_directory": world, "notifications": notifications},
        lambda: None,  # no model verifier: rules only (VER-10)
    )

    async def latest_patient_text(case_id: CaseId) -> UntrustedText | None:
        texts = [t for c, sender, t in cases.communications if c == case_id and sender == "patient"]
        return UntrustedText(value=texts[-1], source="patient_message") if texts else None

    deps = runtime.Dependencies(
        cases=cases,
        policy=policy,
        verifier=verifier,
        notifications=notifications,
        directory=world,
        missed_doses=doses.missed_doses,
        latest_patient_text=latest_patient_text,
    )
    delivery = runtime.Delivery(deps)
    subscribers = SubscriberRegistry()
    subscribers.add("platform.timer_fired", doses.on_timer, name="care.dose_check")
    runtime.register_supervisor(subscribers, deps, delivery)
    agents = {
        AgentKind.GUARDIAN_AGENT: GuardianAgent(),
        AgentKind.PATIENT_COMPANION: CompanionAgent(),
    }
    return Harness(
        world,
        clock,
        uow_factory,
        policy,
        cases,
        notifications,
        Dispatcher(uow_factory, subscribers),
        runtime.AgentRunner(uow_factory, agents, deps),
        delivery,
        verifier,
    )  # type: ignore[arg-type]


async def test_full_flow_through_the_real_bus(harness: Harness) -> None:
    """Criteria 1, 2, 4, 7, 8: timer-started, message bus only, consented content, one trace."""
    await harness.schedule_missed_evenings()  # nobody types anything (criterion 2)
    await harness.pump()

    check_ins = await harness.messages(MessageIntent.CHECK_IN_WITH_PATIENT)
    assert len(check_ins) == 1  # two missed-dose events, one check-in
    assert check_ins[0].sender == "guardian_agent" and check_ins[0].status == "delivered"
    asked = [t for _, sender, t in harness.cases.communications if sender == "patient"]
    assert asked and "සවස" in asked[0]  # the companion asked Amma, in Sinhala
    assert harness.notifications_for(harness.world.nimal) == []

    await harness.amma_replies("මට ඔලුව කැරකෙනවා")  # "I feel dizzy"
    await harness.pump()
    result = (await harness.messages(MessageIntent.CHECK_IN_RESULT))[0]
    assert result.sender == "patient_companion" and result.status == "delivered"

    alert = (await harness.messages(MessageIntent.GUARDIAN_ALERT))[0]
    assert alert.status == MessageStatus.ESCALATED.value  # VER-10: offline, Amma approves first
    assert harness.notifications_for(harness.world.amma_user)[0]["kind"] == "review_required"

    assert await harness.approve_alert()
    to_nimal = harness.notifications_for(harness.world.nimal)
    assert len(to_nimal) == 1
    assert to_nimal[0]["facts"] == {"missed_count": 2, "patient_ok": "no"}
    assert "කැරකෙනවා" not in str(to_nimal)  # Amma's own words never reach Nimal (criterion 4)

    all_messages = await harness.messages()
    assert {m.trace_id for m in all_messages} == {TRACE}  # one trace across every hop (criterion 7)
    assert {m.case_id for m in all_messages} == {next(iter(harness.cases.states))}


async def test_revoked_consent_blocks_the_alert(harness: Harness) -> None:
    """Criterion 3: consent revoked before delivery means no notification."""
    await harness.schedule_missed_evenings()
    await harness.pump()
    await harness.amma_replies("I feel dizzy")
    await harness.pump()
    harness.policy.guardian_grants.pop((harness.world.nimal, harness.world.amma))  # Amma revokes

    assert not await harness.approve_alert()
    assert harness.notifications_for(harness.world.nimal) == []
    assert (await harness.messages(MessageIntent.GUARDIAN_ALERT))[
        0
    ].status == MessageStatus.BLOCKED.value


async def test_guardian_agent_cannot_read_conversations(harness: Harness) -> None:
    """Criterion 4: the guardian side has no path to Amma's conversation."""
    guardian_agent = Principal(
        kind=PrincipalKind.AGENT,
        agent_kind=AgentKind.GUARDIAN_AGENT,
        role=Role.GUARDIAN,
        acting_for=harness.world.nimal,
        patient_scope=harness.world.amma,
        purpose=Purpose.GUARDIAN_MONITORING,
        trace_id=TraceId(TRACE),
    )
    for category in (DataCategory.CONVERSATIONS, DataCategory.CONDITIONS):
        decision = await harness.policy.decide(
            AccessRequest(
                principal=guardian_agent,
                action="read",
                patient_id=harness.world.amma,
                category=category,
                resource_type="Communication",
                purpose=Purpose.GUARDIAN_MONITORING,
            )
        )
        assert not decision.allowed
    assert "conversations" not in {c.value for c in GuardianAgent.spec.data_scope}


async def test_redelivery_and_job_reruns_send_nothing_twice(harness: Harness) -> None:
    """Criterion 5: replayed events and re-run jobs create no duplicate check-ins or alerts."""
    await harness.schedule_missed_evenings()
    await harness.pump()
    await harness.amma_replies("I am fine now")
    await harness.pump()
    before = len(await harness.messages())
    async with harness.factory() as uow:  # replay every delivery and re-run every agent job
        await uow.session.execute(
            update(InboxRow).values(status="pending", available_at=harness.clock.now())
        )
        await uow.session.execute(update(OutboxRow).values(status="pending"))
        await uow.session.execute(
            sql("UPDATE agents_jobs SET status = 'queued', available_at = now() - interval '1 day'")
        )
        await uow.commit()
    await harness.pump()
    assert len(await harness.messages()) == before
    assert len(await harness.messages(MessageIntent.CHECK_IN_WITH_PATIENT)) == 1
    assert len(await harness.messages(MessageIntent.GUARDIAN_ALERT)) == 1


async def test_unanswered_check_in_times_out_to_an_alert(harness: Harness) -> None:
    """Criterion 6: no answer within 30 minutes means the guardian is told it is unclear."""
    await harness.schedule_missed_evenings()
    await harness.pump()
    assert await harness.messages(MessageIntent.GUARDIAN_ALERT) == []

    harness.clock.advance(timedelta(minutes=31))
    await harness.pump()
    alert = (await harness.messages(MessageIntent.GUARDIAN_ALERT))[0]
    assert alert.status == MessageStatus.ESCALATED.value
    assert await harness.approve_alert()
    assert harness.notifications_for(harness.world.nimal)[0]["facts"]["patient_ok"] == "unclear"  # type: ignore[index]

    await harness.amma_replies("I am fine")  # a late answer does not create a second alert
    await harness.pump()
    assert len(await harness.messages(MessageIntent.GUARDIAN_ALERT)) == 1


async def test_a_held_message_is_retried_and_reaches_a_human_never_unchecked(
    harness: Harness,
) -> None:
    """VER-9: the model verifier is down. Even the agent-to-agent check-in is held and retried
    every 2 minutes; after 30 minutes it goes to a human. Nothing moves on unchecked."""

    async def down(state: list[str], questions: dict[str, object]) -> object:
        raise RuntimeError("provider down")

    harness.verifier._decide_fn = lambda: down  # type: ignore[assignment,return-value]
    await harness.schedule_missed_evenings()
    await harness.pump()

    async def status() -> str:
        return (await harness.messages(MessageIntent.CHECK_IN_WITH_PATIENT))[0].status

    assert await status() == MessageStatus.HELD.value
    assert [t for _, sender, t in harness.cases.communications if sender == "patient"] == []
    for _ in range(14):  # 28 minutes of retries: still held
        harness.clock.advance(timedelta(minutes=2))
        await harness.pump()
    assert await status() == "held"
    harness.clock.advance(timedelta(minutes=3))
    await harness.pump()
    assert await status() == "escalated"
    assert harness.notifications_for(harness.world.nimal) == []
    assert harness.notifications_for(harness.world.amma_user)[-1]["kind"] == "review_required"
