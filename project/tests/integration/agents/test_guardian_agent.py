"""Important tier: the guardian agent through the real bus (spec 05 section 4, A04).

Real Postgres outbox, timers, job queue, case service and agent messages. The verifier is the
scripted test verifier (allow) until M16 lands; adherence comes from a fake care port that checks
consent like the real one must.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from suwapath.contracts.agents import AgentMessage
from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import (
    AgentKind,
    CaseStatus,
    DataCategory,
    DoseSlot,
    Language,
    PrincipalKind,
    Purpose,
    Role,
)
from suwapath.contracts.errors import ConsentDenied
from suwapath.contracts.events.catalog import AlertRuleFired, DoseMissed, RedFlagRaised
from suwapath.contracts.ids import CaseId, PatientId, ResourceId, TraceId, UserId
from suwapath.contracts.ports.care import AdherenceDay, AdherenceSummary
from suwapath.contracts.ports.consent import AccessRequest
from suwapath.contracts.ports.verifier import Verdict, VerificationContext
from suwapath.contracts.principal import Principal
from suwapath.contracts.testing.doubles_clinical import ScriptedVerifier
from suwapath.contracts.testing.doubles_core import (
    InMemoryAccessPolicy,
    InMemoryNotificationService,
)
from suwapath.modules.agents.adapters.models import AgentMessageRow
from suwapath.modules.agents.application.context import Dependencies
from suwapath.modules.agents.application.guardian import GuardianAgent
from suwapath.modules.agents.application.runtime import (
    AgentRegistry,
    AgentRunner,
    Delivery,
    register_supervisor,
)
from suwapath.modules.agents.application.skeleton_agents import CompanionAgent
from suwapath.modules.cases.adapters.models import CaseRow
from suwapath.modules.cases.application.service import SqlCaseService
from suwapath.platform.clock import FixedClock
from suwapath.platform.events.dispatcher import Dispatcher
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.gateway.gateway import ModelGateway
from suwapath.platform.gateway.providers.offline import OfflineChatModel
from suwapath.platform.gateway.types import ChoiceAnswer, DecisionResult, Question, Usage
from suwapath.platform.jobs.timers import fire_due_timers
from suwapath.platform.models import AgentJobRow, TimerRow
from suwapath.platform.tracing import set_trace_id
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
TRACE = "c0ffee00" * 4


class RecordingVerifier(ScriptedVerifier):
    """The scripted test verifier (allow), also recording the recipient access it was given."""

    def __init__(self) -> None:
        super().__init__()
        self.access: list[tuple[str, bool, str]] = []

    async def check(self, message: AgentMessage, context: VerificationContext) -> Verdict:
        decision = context.recipient_access
        self.access.append((message.intent.value, decision.allowed, decision.reason))
        return await super().check(message, context)


class FakeJev:
    """A decision model that answers the week question as told and records what it saw."""

    name = "fake-jev"

    def __init__(self, choice: str, confidence: float) -> None:
        self.choice = choice
        self.confidence = confidence
        self.states: list[list[str]] = []

    async def decide(self, state: list[str], questions: dict[str, Question]) -> DecisionResult:
        self.states.append(state)
        answer = ChoiceAnswer(
            choice=self.choice,
            probabilities={self.choice: self.confidence},
            confidence=self.confidence,
        )
        return DecisionResult(
            answers={"week": answer}, usage=Usage(), model=self.name, latency_ms=1
        )


@dataclass
class Family:
    cases: SqlCaseService
    patient: PatientId = field(default_factory=lambda: PatientId(uuid4()))
    amma: UserId = field(default_factory=lambda: UserId(uuid4()))
    son: UserId = field(default_factory=lambda: UserId(uuid4()))

    async def patient_user(self, patient_id: PatientId) -> UserId:
        return self.amma

    async def guardians(self, patient_id: PatientId) -> list[UserId]:
        return [self.son]

    async def case_patient(self, case_id: CaseId) -> PatientId:
        return await self.cases.patient_of(case_id)

    async def language(self, patient_id: PatientId) -> Language:
        return Language.EN

    async def practitioner_user(self, practitioner_role_id: Any) -> UserId | None:
        return None

    async def doctor_case(self, patient_id: PatientId) -> None:
        return None


def week(*missed: int, taken: int = 2) -> list[AdherenceDay]:
    today = date(2026, 11, 28)
    return [
        AdherenceDay(
            day=today - timedelta(days=len(missed) - 1 - n),
            taken=taken,
            missed=m,
            pending=0,
            missed_slots=(DoseSlot.EVENING,) * min(m, 1),
        )
        for n, m in enumerate(missed)
    ]


CONCERNING = week(0, 0, 0, 0, 0, 1, 1)
URGENT = week(0, 0, 0, 0, 0, 1, 1, taken=0)
STABLE = week(0, 0, 0, 0, 0, 0, 0)


class Home:
    """Amma, her son Nimal, the guardian agent and the companion, on the real bus."""

    def __init__(
        self, uow_factory: UnitOfWorkFactory, clock: FixedClock, jev: FakeJev | None = None
    ) -> None:
        set_trace_id(TRACE)
        self.uow_factory = uow_factory
        self.clock = clock
        self.days = CONCERNING
        self.policy = InMemoryAccessPolicy(
            user_roles={},
            agent_scopes={
                AgentKind.GUARDIAN_AGENT.value: GuardianAgent.spec.data_scope,
                AgentKind.PATIENT_COMPANION.value: CompanionAgent.spec.data_scope,
            },
        )
        self.cases = SqlCaseService(uow_factory, lambda: self.policy)
        self.family = Family(self.cases)
        self.policy.patients_by_user[self.family.amma] = self.family.patient
        self.policy.user_roles |= {self.family.amma: Role.PATIENT, self.family.son: Role.GUARDIAN}
        self.grant({DataCategory.ADHERENCE})
        self.notifications = InMemoryNotificationService(now=clock.now)
        self.jev = jev
        self.verifier = RecordingVerifier()
        deps = Dependencies(
            cases=self.cases,
            policy=self.policy,
            verifier=self.verifier,
            notifications=self.notifications,
            directory=self.family,
            missed_doses=None,
            latest_patient_text=self.cases.latest_patient_text,
            adherence=self.adherence,
        )
        registry = AgentRegistry([GuardianAgent(), CompanionAgent()])
        subscribers = SubscriberRegistry()
        register_supervisor(subscribers, deps, Delivery(deps), registry.kinds())
        self.dispatcher = Dispatcher(uow_factory, subscribers)
        gateway = ModelGateway(OfflineChatModel(), decision=jev, clock=clock)
        self.runner = AgentRunner(uow_factory, registry, deps, gateway=gateway)

    def grant(self, categories: set[DataCategory]) -> None:
        self.policy.guardian_grants[(self.family.son, self.family.patient)] = frozenset(categories)

    async def adherence(
        self, principal: Principal, patient_id: PatientId, days: int
    ) -> AdherenceSummary:
        decision = await self.policy.decide(
            AccessRequest(
                principal=principal,
                action="read",
                patient_id=patient_id,
                category=DataCategory.ADHERENCE,
                resource_type="AdherenceSummary",
                purpose=principal.purpose,
            )
        )
        if not decision.allowed:
            raise ConsentDenied()
        return AdherenceSummary(
            patient_id=patient_id,
            days=tuple(self.days),
            missed_by_slot={DoseSlot.EVENING: sum(d.missed for d in self.days)},
            current_streak_days=0,
        )

    async def publish(self, payload: Any) -> None:
        async with self.uow_factory() as uow:
            uow.publish(payload, aggregate_id="test", patient_id=self.family.patient)
            await uow.commit()

    async def dose_missed(self) -> None:
        await self.publish(
            DoseMissed(
                administration_id=ResourceId(uuid4()),
                statement_id=ResourceId(uuid4()),
                slot=DoseSlot.EVENING,
                scheduled_at=self.clock.now() - timedelta(hours=2),
                patient_id=self.family.patient,
            )
        )

    async def pump(self) -> None:
        for _ in range(50):
            busy = await fire_due_timers(self.uow_factory) + await self.dispatcher.drain()
            while await self.runner.run_once("test-worker"):
                busy += 1
            if not busy:
                return
        raise AssertionError("system did not settle")

    async def later(self, delta: timedelta) -> None:
        self.clock.advance(delta)
        await self.pump()

    async def amma_says(self, text: str) -> None:
        amma = Principal(
            kind=PrincipalKind.USER,
            user_id=self.family.amma,
            role=Role.PATIENT,
            acting_for=self.family.amma,
            patient_scope=self.family.patient,
            purpose=Purpose.CARE_NAVIGATION,
            trace_id=TraceId(TRACE),
        )
        case_id = await self.only_case()
        await self.cases.add_patient_message(
            amma, case_id, UntrustedText(value=text, source="patient_message")
        )
        await self.pump()

    async def only_case(self) -> CaseId:
        async with self.uow_factory() as uow:
            ids = (await uow.session.execute(select(CaseRow.id))).scalars().all()
        assert len(ids) == 1
        return CaseId(ids[0])

    async def case_count(self) -> int:
        async with self.uow_factory() as uow:
            query = select(func.count()).select_from(CaseRow)
            return int((await uow.session.execute(query)).scalar_one())

    async def intents(self) -> list[str]:
        async with self.uow_factory() as uow:
            query = select(AgentMessageRow.intent).order_by(AgentMessageRow.created_at)
            return list((await uow.session.execute(query)).scalars())

    async def statuses(self, intent: str) -> list[str]:
        async with self.uow_factory() as uow:
            query = select(AgentMessageRow.status).where(AgentMessageRow.intent == intent)
            return list((await uow.session.execute(query)).scalars())

    async def job_errors(self) -> list[str]:
        async with self.uow_factory() as uow:
            query = select(AgentJobRow.error_code).where(AgentJobRow.status != "done")
            return [str(e) for e in (await uow.session.execute(query)).scalars()]

    async def timer_status(self, kind: str) -> str:
        async with self.uow_factory() as uow:
            query = select(TimerRow.status).where(TimerRow.kind == kind)
            return str((await uow.session.execute(query)).scalar_one())

    def alerts(self) -> list[dict[str, Any]]:
        return [n.params for n in self.notifications.items if n.user_id == self.family.son]


@pytest.fixture
def home(uow_factory: UnitOfWorkFactory, clock: FixedClock) -> Home:
    return Home(uow_factory, clock)


# ----- check-in first ----------------------------------------------------------------------------


async def test_a_concerning_week_checks_in_with_amma_before_telling_her_son(home: Home) -> None:
    await home.dose_missed()
    await home.pump()
    assert await home.intents() == ["check_in_with_patient"]
    assert home.alerts() == []  # not yet: Amma is asked first
    await home.amma_says("I am fine, I forgot")
    assert home.alerts() == [
        {
            "level": "info",
            "facts": {"missed_count": 2, "slots_missed": ["evening"], "patient_ok": "yes"},
            "suggested_action_key": "alert.no_action",
        }
    ]
    son_view = await home.cases.get(_son(home), await home.only_case())
    assert son_view.guardian_status is not None
    assert son_view.guardian_status.status == "alerted"


async def test_no_answer_waits_for_the_follow_up_then_alerts_once(home: Home) -> None:
    await home.dose_missed()
    await home.pump()
    await home.later(timedelta(minutes=31))  # the request deadline passes
    assert home.alerts() == []  # more time for Amma
    await home.later(timedelta(hours=2, minutes=1))
    assert [a["facts"]["patient_ok"] for a in home.alerts()] == ["no_response"]
    assert home.alerts()[0]["level"] == "concern"
    await home.later(timedelta(hours=3))
    assert len(home.alerts()) == 1


async def test_an_answer_after_the_deadline_cancels_the_follow_up(home: Home) -> None:
    await home.dose_missed()
    await home.pump()
    await home.later(timedelta(minutes=31))
    await home.amma_says("dizzy since morning")
    assert [a["level"] for a in home.alerts()] == ["concern"]
    async with home.uow_factory() as uow:
        status = (
            await uow.session.execute(
                select(TimerRow.status).where(TimerRow.kind == "guardian_checkin_followup")
            )
        ).scalar_one()
    assert status == "cancelled"
    await home.later(timedelta(hours=3))
    assert len(home.alerts()) == 1


async def test_one_check_in_per_day_however_many_doses_are_missed(home: Home) -> None:
    for _ in range(3):
        await home.dose_missed()
    await home.pump()
    assert await home.intents() == ["check_in_with_patient"]
    assert await home.case_count() == 1


# ----- urgent, quiet and unconsented weeks -------------------------------------------------------


async def test_an_urgent_week_alerts_the_son_at_once(home: Home) -> None:
    home.days = URGENT
    await home.dose_missed()
    await home.pump()
    assert await home.intents() == ["guardian_alert"]
    assert [(a["level"], a["suggested_action_key"]) for a in home.alerts()] == [
        ("urgent", "alert.call_patient_now")
    ]


async def test_a_stable_week_contacts_nobody(home: Home) -> None:
    home.days = STABLE
    await home.dose_missed()
    await home.pump()
    assert await home.intents() == []
    assert await home.case_count() == 0


async def test_without_an_adherence_grant_the_agent_does_nothing(home: Home) -> None:
    home.grant({DataCategory.APPOINTMENTS})
    await home.dose_missed()
    await home.pump()
    assert await home.intents() == []
    assert home.alerts() == []


async def test_consent_revoked_before_delivery_blocks_the_alert(home: Home) -> None:
    await home.dose_missed()
    await home.pump()
    home.grant(set())  # Amma withdraws the grant while the check-in is open
    await home.amma_says("I am fine")
    assert home.alerts() == []
    assert await home.statuses("guardian_alert") == []  # consent wins: nothing is drafted
    assert await home.job_errors() == []  # a clean stop, not a crash and retry
    # The result going to the guardian agent was checked as the son, who lost the grant.
    assert ("check_in_result", False, "guardian_grant_missing") in home.verifier.access


# ----- Jev for the unclear middle ----------------------------------------------------------------


async def test_jev_can_settle_a_borderline_week(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    jev = FakeJev("watch", 0.85)
    home = Home(uow_factory, clock, jev)
    await home.dose_missed()
    await home.pump()
    assert await home.intents() == []  # Jev read the pattern as a one-off
    assert len(jev.states) == 1
    assert str(home.family.patient) not in "\n".join(jev.states[0])


async def test_an_unsure_jev_keeps_the_rules(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    home = Home(uow_factory, clock, FakeJev("watch", 0.3))
    await home.dose_missed()
    await home.pump()
    assert await home.intents() == ["check_in_with_patient"]


async def test_clear_weeks_never_call_jev(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    jev = FakeJev("stable", 0.99)
    home = Home(uow_factory, clock, jev)
    home.days = URGENT
    await home.dose_missed()
    await home.pump()
    assert jev.states == []  # an urgent week can never be talked down
    assert [a["level"] for a in home.alerts()] == ["urgent"]


# ----- other triggers ----------------------------------------------------------------------------


async def test_a_guardian_rule_starts_a_check_in_even_in_a_quiet_week(home: Home) -> None:
    home.days = STABLE
    await home.publish(
        AlertRuleFired(
            rule_id=ResourceId(uuid4()),
            rule_type="missed_doses",
            patient_id=home.family.patient,
            guardian_id=home.family.son,
        )
    )
    await home.pump()
    assert await home.intents() == ["check_in_with_patient"]


async def test_a_red_flag_stands_the_follow_up_down(home: Home) -> None:
    await home.dose_missed()
    await home.pump()
    await home.later(timedelta(minutes=31))
    case_id = await home.only_case()
    await home.publish(
        RedFlagRaised(
            case_id=case_id, patient_id=home.family.patient, rule_ids=("RF-1",), urgency="emergency"
        )
    )
    await home.pump()
    await home.later(timedelta(hours=3))
    assert home.alerts() == []  # the companion sends the emergency alert, not a routine one
    assert await home.timer_status("guardian_checkin_followup") == "cancelled"


def _son(home: Home) -> Principal:
    return Principal(
        kind=PrincipalKind.USER,
        user_id=home.family.son,
        role=Role.GUARDIAN,
        acting_for=home.family.son,
        purpose=Purpose.GUARDIAN_MONITORING,
        trace_id=TraceId(TRACE),
    )


async def test_the_case_stays_open_for_the_doctor_flow(home: Home) -> None:
    await home.dose_missed()
    await home.pump()
    state = await home.cases.get(_son(home), await home.only_case())
    assert state.status is CaseStatus.OPEN
