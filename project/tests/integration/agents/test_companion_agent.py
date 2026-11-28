"""Important tier: the patient companion through the real bus (spec 05 section 4, A03).

Safety first, intake, routing with a hold and a proposal, the emergency lane, answering the guardian
agent's check-in, and si and en. Navigation is scripted (M07 is not built yet); the verifier is
the scripted test verifier until M16 lands; safety is the keyword double until M06 lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from suwapath.contracts.agents import CompanionReply
from suwapath.contracts.cases import CaseEventKind
from suwapath.contracts.common import Page, UntrustedText
from suwapath.contracts.enums import (
    AgentKind,
    CaseStatus,
    DataCategory,
    DoseSlot,
    Language,
    PrincipalKind,
    Purpose,
    Role,
    Urgency,
)
from suwapath.contracts.events.catalog import DoseMissed
from suwapath.contracts.fhir.base import GeoPoint
from suwapath.contracts.ids import (
    CaseId,
    PatientId,
    PractitionerRoleId,
    ResourceId,
    TraceId,
    UserId,
)
from suwapath.contracts.ports.care import AdherenceDay, AdherenceSummary
from suwapath.contracts.ports.navigation import (
    DoctorOption,
    Finding,
    NavigationQuestion,
    NavigationStep,
)
from suwapath.contracts.principal import Principal
from suwapath.contracts.testing.doubles_clinical import (
    InMemoryBookingService,
    KeywordSafetyService,
    ScriptedVerifier,
)
from suwapath.contracts.testing.doubles_core import (
    InMemoryAccessPolicy,
    InMemoryNotificationService,
)
from suwapath.modules.agents.adapters.models import AgentMessageRow
from suwapath.modules.agents.application.companion import CompanionAgent
from suwapath.modules.agents.application.context import Dependencies, PatientProfile
from suwapath.modules.agents.application.guardian import GuardianAgent
from suwapath.modules.agents.application.runtime import (
    AgentRegistry,
    AgentRunner,
    Delivery,
    register_supervisor,
)
from suwapath.modules.cases.adapters.models import CaseEventRow, CaseRow, CommunicationRow
from suwapath.modules.cases.application.service import SqlCaseService
from suwapath.platform.clock import FixedClock
from suwapath.platform.events.dispatcher import Dispatcher
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.gateway.gateway import ModelGateway
from suwapath.platform.gateway.providers.offline import OfflineChatModel
from suwapath.platform.gateway.types import ChoiceAnswer, DecisionResult, Question, Usage
from suwapath.platform.jobs.timers import fire_due_timers
from suwapath.platform.models import AgentJobRow
from suwapath.platform.tracing import set_trace_id
from suwapath.platform.uow import UnitOfWorkFactory
from tests.integration.agents import companion_fixtures as fx

pytestmark = pytest.mark.integration
TRACE = "a3a3a3a3" * 4
K = CaseEventKind


@dataclass
class ScriptedNavigation:
    steps: list[NavigationStep]
    doctors: list[DoctorOption] = field(default_factory=list)
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    async def start(
        self, principal: Any, case_id: Any, text: Any, context: Any, language: Any
    ) -> NavigationStep:
        self.calls.append(("start", text.value, context.age_years))
        return self.steps.pop(0)

    async def answer(
        self, principal: Any, case_id: Any, concept: str, answer: Any, context: Any = None
    ) -> NavigationStep:
        self.calls.append(("answer", concept, answer))
        return self.steps.pop(0)

    async def result(self, principal: Any, case_id: Any) -> None:
        return None

    async def find_doctors(self, query: Any, cursor: Any, limit: int) -> Page[DoctorOption]:
        self.calls.append(("find_doctors", query.specialty.value, query.date_to - query.date_from))
        return Page(items=self.doctors[:limit])

    async def plan_trip(self, *args: Any) -> list[Any]:
        return []


class FakeJev:
    name = "fake-jev"

    def __init__(self, choice: str, confidence: float = 0.9) -> None:
        self.choice, self.confidence = choice, confidence
        self.states: list[list[str]] = []

    async def decide(self, state: list[str], questions: dict[str, Question]) -> DecisionResult:
        self.states.append(state)
        answer = ChoiceAnswer(choice=self.choice, probabilities={}, confidence=self.confidence)
        return DecisionResult(
            answers={**{k: answer for k in questions}}, usage=Usage(), model=self.name, latency_ms=1
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

    async def practitioner_user(self, practitioner_role_id: Any) -> None:
        return None

    async def doctor_case(self, patient_id: PatientId) -> None:
        return None

    async def profile(self, patient_id: PatientId) -> PatientProfile:
        return PatientProfile(
            first_name=fx.AMMA_FIRST_NAME,
            age_years=fx.AMMA_AGE,
            sex="female",
            home=GeoPoint(lat=6.95, lng=79.86),
        )


class Home:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: FixedClock,
        steps: list[NavigationStep],
        jev: FakeJev | None = None,
    ) -> None:
        set_trace_id(TRACE)
        self.uow_factory, self.clock = uow_factory, clock
        self.policy = InMemoryAccessPolicy(
            agent_scopes={
                AgentKind.PATIENT_COMPANION.value: CompanionAgent.spec.data_scope,
                AgentKind.GUARDIAN_AGENT.value: GuardianAgent.spec.data_scope,
            }
        )
        self.cases = SqlCaseService(uow_factory, lambda: self.policy)
        self.family = Family(self.cases)
        f = self.family
        self.policy.patients_by_user[f.amma] = f.patient
        self.policy.user_roles |= {f.amma: Role.PATIENT, f.son: Role.GUARDIAN}
        self.policy.guardian_grants[(f.son, f.patient)] = frozenset(
            {DataCategory.ADHERENCE, DataCategory.DEMOGRAPHICS}
        )
        self.booking = InMemoryBookingService(now=clock.now)
        role = PractitionerRoleId(uuid4())
        at = clock.now() + timedelta(hours=3)
        slot = self.booking.add_slot(role, at)
        self.navigation = ScriptedNavigation(list(steps), [fx.doctor(slot, at, role)])
        self.safety = KeywordSafetyService()
        self.notifications = InMemoryNotificationService(now=clock.now)
        self.missed = [AdherenceDay(day=clock.now().date(), taken=2, missed=0, pending=0)]
        deps = Dependencies(
            cases=self.cases,
            policy=self.policy,
            verifier=ScriptedVerifier(),
            notifications=self.notifications,
            directory=self.family,
            missed_doses=None,
            latest_patient_text=self.cases.latest_patient_text,
            adherence=self.adherence,
            safety=self.safety,
            navigation=self.navigation,
            booking=self.booking,
        )
        registry = AgentRegistry([CompanionAgent(), GuardianAgent()])
        subscribers = SubscriberRegistry()
        register_supervisor(subscribers, deps, Delivery(deps), registry.kinds())
        self.dispatcher = Dispatcher(uow_factory, subscribers)
        gateway = ModelGateway(OfflineChatModel(), decision=jev, clock=clock)
        self.runner = AgentRunner(uow_factory, registry, deps, gateway=gateway)

    async def adherence(
        self, principal: Principal, patient: PatientId, days: int
    ) -> AdherenceSummary:
        return AdherenceSummary(
            patient_id=patient, days=tuple(self.missed), missed_by_slot={}, current_streak_days=0
        )

    @property
    def amma(self) -> Principal:
        f = self.family
        return Principal(
            kind=PrincipalKind.USER,
            user_id=f.amma,
            role=Role.PATIENT,
            acting_for=f.amma,
            patient_scope=f.patient,
            purpose=Purpose.CARE_NAVIGATION,
            trace_id=TraceId(TRACE),
        )

    async def pump(self) -> None:
        for _ in range(50):
            busy = await fire_due_timers(self.uow_factory) + await self.dispatcher.drain()
            while await self.runner.run_once("test-worker"):
                busy += 1
            if not busy:
                await self.no_job_errors()
                return
        raise AssertionError("system did not settle")

    async def no_job_errors(self) -> None:
        """Green must mean green: no agent job failed and retried behind the result."""
        async with self.uow_factory() as uow:
            query = select(AgentJobRow.agent_kind, AgentJobRow.error_code).where(
                (AgentJobRow.status != "done") | (AgentJobRow.attempts > 1)
            )
            failed = (await uow.session.execute(query)).all()
        assert not failed, failed

    async def opens(self, text: str, language: Language = Language.EN) -> CaseId:
        state = await self.cases.open(
            self.amma,
            self.family.patient,
            UntrustedText(value=text, source="patient_message"),
            language,
        )
        await self.pump()
        return state.id

    async def says(self, text: str) -> None:
        await self.cases.add_patient_message(
            self.amma, await self.only_case(), UntrustedText(value=text, source="patient_message")
        )
        await self.pump()

    async def only_case(self) -> CaseId:
        async with self.uow_factory() as uow:
            ids = (await uow.session.execute(select(CaseRow.id))).scalars().all()
        assert len(ids) == 1
        return CaseId(ids[0])

    async def replies(self) -> list[CompanionReply]:
        async with self.uow_factory() as uow:
            rows = (
                await uow.session.execute(
                    select(CommunicationRow.payload_enc)
                    .where(CommunicationRow.recipient == "patient")
                    .order_by(CommunicationRow.created_at, CommunicationRow.id)
                )
            ).scalars()
            return [CompanionReply.model_validate_json(r.decode()) for r in rows]

    async def events(self) -> list[str]:
        async with self.uow_factory() as uow:
            rows = await uow.session.execute(
                select(CaseEventRow.kind).order_by(CaseEventRow.version)
            )
            return list(rows.scalars())

    async def intents(self) -> list[str]:
        async with self.uow_factory() as uow:
            query = select(AgentMessageRow.intent).order_by(AgentMessageRow.created_at)
            return list((await uow.session.execute(query)).scalars())

    async def state(self) -> Any:
        return await self.cases.get(self.amma, await self.only_case())

    def notified(self, user: UserId) -> list[tuple[str, dict[str, Any]]]:
        return [(n.kind, n.params) for n in self.notifications.items if n.user_id == user]


def home_for(
    uow_factory: UnitOfWorkFactory,
    clock: FixedClock,
    steps: list[NavigationStep],
    jev: FakeJev | None = None,
) -> Home:
    return Home(uow_factory, clock, steps, jev)


# ----- Amma: intake, routing, a hold and a proposal ---------------------------------------------


async def test_amma_in_english_is_asked_routed_and_offered_a_held_slot(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    home = home_for(uow_factory, clock, list(fx.AMMA_EN["steps"]))
    await home.opens(fx.AMMA_EN["first"])
    question = (await home.replies())[-1]
    assert question.kind == "question"
    assert question.text == "Since when have you felt dizzy?"
    assert [s.label for s in question.suggestions] == ["Not sure"]

    await home.says(fx.AMMA_EN["since"])
    result = (await home.replies())[-1]
    assert result.kind == "result"
    # RF-GER-003 (dizzy, over 65) set an urgent floor; the engine's routine result is raised.
    assert result.text.startswith("Please see a doctor today.")
    assert "Dr. Perera" in result.text
    actions = [(s.action_kind, (s.action_id or "").split(":")[0]) for s in result.suggestions]
    assert actions == [("book_slot", "hold"), ("show_doctors", "doctors")]
    state = await home.state()
    assert state.status is CaseStatus.ASSESSED
    assert state.urgency is Urgency.URGENT_24H
    assert {f.key for f in state.facts} == {"red_flag.RF-GER-003", "dizziness_since"}
    assert len(home.booking.holds) == 1  # held, never booked: Amma taps to confirm (BOOK-2)
    assert home.booking.appointment_store == {}
    assert ("find_doctors", "general_medicine", timedelta(0)) in home.navigation.calls


async def test_amma_in_sinhala_gets_sinhala_replies(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    home = home_for(uow_factory, clock, list(fx.AMMA_SI["steps"]))
    await home.opens(fx.AMMA_SI["first"], Language.SI)
    await home.says(fx.AMMA_SI["since"])
    question, result = await home.replies()
    assert question.language is Language.SI
    assert question.text == "ඔබට කරකැවිල්ල දැනෙන්නේ කවදා සිට ද?"
    assert result.text.startswith("කරුණාකර සති 2ක් ඇතුළත")
    assert ("answer", "dizziness_since", "2 days") in home.navigation.calls


async def test_the_engine_gets_the_patients_age_for_its_rules(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    home = home_for(uow_factory, clock, list(fx.AMMA_EN["steps"]))
    await home.opens(fx.AMMA_EN["first"])
    assert home.navigation.calls[0] == ("start", fx.AMMA_EN["first"], fx.AMMA_AGE)


# ----- unclear answers ----------------------------------------------------------------------------


async def test_an_unclear_answer_is_asked_again_without_a_model(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    home = home_for(uow_factory, clock, [fx.question("fever"), fx.result(Urgency.ROUTINE)])
    await home.opens("my head is heavy")
    await home.says("hmm my knee also")
    last = (await home.replies())[-1]
    assert last.kind == "clarify"
    assert [s.label for s in last.suggestions] == ["Yes", "No", "Not sure"]
    assert [c[0] for c in home.navigation.calls] == ["start"]  # nothing guessed


async def test_jev_reads_an_unclear_answer_when_it_is_sure(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    jev = FakeJev("yes")
    home = home_for(uow_factory, clock, [fx.question("fever"), fx.result(Urgency.ROUTINE)], jev)
    await home.opens("my head is heavy")
    await home.says("since last night I am burning up")
    assert ("answer", "fever", "yes") in home.navigation.calls
    assert jev.states and "Reply (data): since last night I am burning up" in jev.states[0]


async def test_not_sure_is_passed_on_as_not_sure(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    home = home_for(uow_factory, clock, [fx.question("fever"), fx.result(Urgency.ROUTINE)])
    await home.opens("my head is heavy")
    await home.says("I don't know")
    assert ("answer", "fever", "unsure") in home.navigation.calls


# ----- the emergency lane (SAF-10) ---------------------------------------------------------------


async def test_chest_pain_stops_everything_and_tells_the_family(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    home = home_for(uow_factory, clock, [])
    await home.opens("I have chest pain and sweating")
    reply = (await home.replies())[-1]
    assert reply.kind == "emergency"
    assert reply.text.startswith("This may be an emergency. Call 1990 now.")
    assert [s.action_kind for s in reply.suggestions] == ["call_1990", "call_guardian"]
    assert (await home.state()).status is CaseStatus.EMERGENCY
    assert home.navigation.calls == []  # no intake questions, no model, no booking
    assert home.booking.holds == {}
    son = home.notified(home.family.son)
    assert son == [
        (
            "emergency_alert",
            {"patient_first_name": "Kamala", "contact_hint_key": "alert.call_patient_now"},
        )
    ]
    await home.says("chest pain still")  # a second message repeats the guidance only
    assert len(home.notified(home.family.son)) == 1
    assert (await home.replies())[-1].kind == "emergency"


async def test_the_engines_emergency_stop_is_honoured(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    home = home_for(uow_factory, clock, [fx.EMERGENCY_STOP])
    await home.opens("my arm is numb")
    assert (await home.state()).status is CaseStatus.EMERGENCY
    assert "red_flag_raised" in await home.events()


# ----- answering the guardian agent's check-in ---------------------------------------------------


async def _missed_week(home: Home) -> None:
    today = home.clock.now().date()
    home.missed = [
        AdherenceDay(
            day=today - timedelta(days=1),
            taken=1,
            missed=1,
            pending=0,
            missed_slots=(DoseSlot.EVENING,),
        ),
        AdherenceDay(day=today, taken=1, missed=1, pending=0, missed_slots=(DoseSlot.EVENING,)),
    ]
    async with home.uow_factory() as uow:
        uow.publish(
            DoseMissed(
                administration_id=ResourceId(uuid4()),
                statement_id=ResourceId(uuid4()),
                slot=DoseSlot.EVENING,
                scheduled_at=home.clock.now() - timedelta(hours=2),
                patient_id=home.family.patient,
            ),
            aggregate_id="t",
            patient_id=home.family.patient,
        )
        await uow.commit()
    await home.pump()


async def test_amma_feeling_fine_is_reported_without_her_words(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    home = home_for(uow_factory, clock, [])
    await _missed_week(home)
    assert (await home.replies())[-1].kind == "check_in"
    await home.says("I feel all right, I forgot them")
    assert (await home.replies())[-1].kind == "info"
    alerts = home.notified(home.family.son)
    assert [(kind, p["level"], p["facts"]["patient_ok"]) for kind, p in alerts] == [
        ("guardian_alert", "info", "yes")
    ]
    assert "forgot" not in repr(alerts)
    assert home.navigation.calls == []


async def test_amma_not_well_starts_intake_and_raises_the_alert(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    home = home_for(uow_factory, clock, [fx.question("dizziness_since", "duration")])
    await _missed_week(home)
    await home.says("not well, dizzy")
    alerts = home.notified(home.family.son)
    assert [(p["level"], p["facts"]["patient_ok"]) for _, p in alerts] == [("urgent", "no")]
    assert home.navigation.calls[0][0] == "start"
    assert (await home.replies())[-1].kind == "question"
    assert "check_in_result" in await home.intents()


async def test_findings_from_the_first_message_become_case_facts_and_text_is_shown(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    """NAV-4: the engine keeps no state; the companion records the findings it returns, and shows
    the reviewed question text when the engine gives one."""
    step = NavigationStep(
        question=NavigationQuestion(
            concept="cough",
            question_key="nav.cough",
            answer_type="yes_no",
            text="Do you have a cough?",
        ),
        findings=(Finding(concept="fever", present=True), Finding(concept="chills", present=False)),
    )
    home = home_for(uow_factory, clock, [step])
    await home.opens("I have had fever for a day")
    assert (await home.replies())[-1].text == "Do you have a cough?"
    facts = {(f.key, f.present, f.value) for f in (await home.state()).facts}
    assert {("fever", True, "mentioned"), ("chills", False, "mentioned")} <= facts
