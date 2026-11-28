"""Important tier: the doctor assistant on the real bus (spec 05 section 4, spec 16 steps 4 to 8).

Booking makes a brief; a missing home glucose is asked of the patient and comes back with its
source; the doctor's own words go out only after approval (AGT-30), through a LangGraph pause.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from suwapath.contracts.agents import CompanionReply
from suwapath.contracts.briefs import BriefSection
from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import (
    AgentKind,
    CaseStatus,
    Language,
    PrincipalKind,
    Purpose,
    Role,
    Urgency,
    VerdictKind,
)
from suwapath.contracts.events.catalog import AppointmentBooked
from suwapath.contracts.fhir.base import CodeableConcept, Coding, Meta, Quantity, Reference
from suwapath.contracts.fhir.resources import (
    AllergyIntolerance,
    Condition,
    Dosage,
    DoseTiming,
    MedicationStatement,
    Observation,
)
from suwapath.contracts.ids import (
    AppointmentId,
    CaseId,
    PatientId,
    PractitionerId,
    PractitionerRoleId,
    ResourceId,
    TraceId,
    UserId,
)
from suwapath.contracts.ports.care import AdherenceSummary
from suwapath.contracts.ports.identity import UserAccount
from suwapath.contracts.principal import Principal
from suwapath.contracts.testing.doubles_clinical import (
    InMemoryBookingService,
    InMemoryRecordService,
    KeywordSafetyService,
    ScriptedVerifier,
)
from suwapath.contracts.testing.doubles_core import (
    InMemoryAccessPolicy,
    InMemoryNotificationService,
)
from suwapath.modules.agents.adapters.models import AgentMessageRow
from suwapath.modules.agents.api.router import build_router
from suwapath.modules.agents.application.companion import CompanionAgent
from suwapath.modules.agents.application.context import Dependencies, PatientProfile
from suwapath.modules.agents.application.doctor_assistant import DoctorAssistant
from suwapath.modules.agents.application.runtime import (
    AgentRegistry,
    AgentRunner,
    Delivery,
    register_supervisor,
)
from suwapath.modules.cases.adapters.models import CommunicationRow
from suwapath.modules.cases.application.service import SqlCaseService
from suwapath.modules.cases.application.subscribers import CaseSubscribers
from suwapath.platform.clock import FixedClock
from suwapath.platform.config import Settings
from suwapath.platform.events.dispatcher import Dispatcher
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.http.app import create_app
from suwapath.platform.jobs.timers import fire_due_timers
from suwapath.platform.models import AgentJobRow
from suwapath.platform.tracing import set_trace_id
from suwapath.platform.uow import UnitOfWorkFactory
from tests.integration.agents import companion_fixtures as fx
from tests.integration.agents.test_companion_agent import ScriptedNavigation

pytestmark = pytest.mark.integration
TRACE = "d0c70000" * 4
DOCTOR_TOKEN = "doctor"  # a test session name, not a secret  # noqa: S105
NOTES = "Please check your sugar every morning and bring the readings on Monday."


@dataclass
class Directory:
    cases: SqlCaseService
    patient: PatientId = field(default_factory=lambda: PatientId(uuid4()))
    amma: UserId = field(default_factory=lambda: UserId(uuid4()))
    doctor: UserId = field(default_factory=lambda: UserId(uuid4()))
    practitioner: PractitionerId = field(default_factory=lambda: PractitionerId(uuid4()))

    async def patient_user(self, patient_id: PatientId) -> UserId:
        return self.amma

    async def guardians(self, patient_id: PatientId) -> list[UserId]:
        return []

    async def case_patient(self, case_id: CaseId) -> PatientId:
        return await self.cases.patient_of(case_id)

    async def language(self, patient_id: PatientId) -> Language:
        return Language.SI

    async def practitioner_user(self, practitioner_role_id: Any) -> UserId:
        return self.doctor

    async def practitioner_of(self, user_id: UserId) -> PractitionerId | None:
        return self.practitioner if user_id == self.doctor else None

    booked: CaseId | None = None

    async def doctor_case(self, patient_id: PatientId) -> tuple[CaseId, UserId] | None:
        return (self.booked, self.doctor) if self.booked else None

    async def profile(self, patient_id: PatientId) -> PatientProfile:
        return PatientProfile(first_name=fx.AMMA_FIRST_NAME, age_years=fx.AMMA_AGE)


class Clinic:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: FixedClock,
        verdict: VerdictKind = VerdictKind.ALLOW,
    ) -> None:
        set_trace_id(TRACE)
        self.uow_factory, self.clock = uow_factory, clock
        self.policy = InMemoryAccessPolicy(
            agent_scopes={
                AgentKind.PATIENT_COMPANION.value: CompanionAgent.spec.data_scope,
                AgentKind.DOCTOR_ASSISTANT.value: DoctorAssistant.spec.data_scope,
            }
        )
        self.cases = SqlCaseService(uow_factory, lambda: self.policy)
        self.dir = Directory(self.cases)
        d = self.dir
        self.policy.patients_by_user[d.amma] = d.patient
        self.policy.user_roles |= {d.amma: Role.PATIENT, d.doctor: Role.DOCTOR}
        self.policy.treating.add((d.doctor, d.patient))
        self.record = InMemoryRecordService(self.policy)
        self._amma_record()
        self.notifications = InMemoryNotificationService(now=clock.now)
        self.navigation = ScriptedNavigation([fx.result(Urgency.SOON_72H)])
        deps = Dependencies(
            cases=self.cases,
            policy=self.policy,
            verifier=ScriptedVerifier(verdict=verdict),
            notifications=self.notifications,
            directory=self.dir,
            missed_doses=None,
            latest_patient_text=self.cases.latest_patient_text,
            latest_patient_message=self.cases.latest_patient_message,
            adherence=self.adherence,
            safety=KeywordSafetyService(),
            navigation=self.navigation,
            booking=InMemoryBookingService(now=clock.now),
            record=self.record,
        )
        self.deps = deps
        registry = AgentRegistry([CompanionAgent(), DoctorAssistant()])
        subscribers = SubscriberRegistry()
        register_supervisor(subscribers, deps, Delivery(deps), registry.kinds())
        CaseSubscribers(self.cases, uow_factory).register(subscribers)
        self.dispatcher = Dispatcher(uow_factory, subscribers)
        self.runner = AgentRunner(uow_factory, registry, deps, checkpointer=InMemorySaver())
        app = create_app(
            Settings(_env_file=None),
            token_verifier=self.verify,
            routers=[build_router(uow_factory, lambda: deps)],
        )
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    def _amma_record(self) -> None:
        d, meta, now = (
            self.dir,
            Meta(last_updated=self.clock.now(), source="record"),
            self.clock.now(),
        )

        def put(resource: Any) -> Any:
            self.record.put(resource)
            return resource

        self.diabetes = put(
            Condition(
                id=ResourceId(uuid4()),
                meta=meta,
                patient_id=d.patient,
                code=CodeableConcept(text="Type 2 diabetes", coding=[Coding(code="44054006")]),
                clinical_status="active",
            )
        )
        put(
            MedicationStatement(
                id=ResourceId(uuid4()),
                meta=meta,
                patient_id=d.patient,
                medication=CodeableConcept(text="Metformin"),
                dosage=Dosage(text="500 mg twice daily", timing=DoseTiming()),
                status="active",
            )
        )
        self.penicillin = put(
            AllergyIntolerance(
                id=ResourceId(uuid4()),
                meta=meta,
                patient_id=d.patient,
                code=CodeableConcept(text="Penicillin"),
                criticality="high",
            )
        )
        put(
            Observation(
                id=ResourceId(uuid4()),
                meta=meta,
                patient_id=d.patient,
                code=CodeableConcept(text="HbA1c"),
                value=Quantity(value=8.1, unit="%"),
                effective_at=now - timedelta(days=20),
                derived_from=Reference(type="DiagnosticReport", id=ResourceId(uuid4())),
            )
        )

    async def adherence(
        self, principal: Principal, patient: PatientId, days: int
    ) -> AdherenceSummary:
        return AdherenceSummary(
            patient_id=patient, days=(), missed_by_slot={}, current_streak_days=0
        )

    async def verify(self, token: str) -> UserAccount:
        accounts = {
            "doctor": UserAccount(
                id=self.dir.doctor, role=Role.DOCTOR, display_name="Dr", language=Language.EN
            ),
            "other-doctor": UserAccount(
                id=UserId(uuid4()), role=Role.DOCTOR, display_name="Dr2", language=Language.EN
            ),
        }
        return accounts[token]

    @property
    def amma(self) -> Principal:
        d = self.dir
        return Principal(
            kind=PrincipalKind.USER,
            user_id=d.amma,
            role=Role.PATIENT,
            acting_for=d.amma,
            patient_scope=d.patient,
            purpose=Purpose.CARE_NAVIGATION,
            trace_id=TraceId(TRACE),
        )

    async def pump(self) -> None:
        for _ in range(60):
            busy = await fire_due_timers(self.uow_factory) + await self.dispatcher.drain()
            while await self.runner.run_once("test-worker"):
                busy += 1
            if not busy:
                async with self.uow_factory() as uow:
                    query = select(AgentJobRow.agent_kind, AgentJobRow.error_code).where(
                        (AgentJobRow.status != "done") | (AgentJobRow.attempts > 1)
                    )
                    failed = (await uow.session.execute(query)).all()
                assert not failed, failed
                return
        raise AssertionError("system did not settle")

    async def booked_case(self) -> CaseId:
        """Spec 16 steps 1 to 4: Amma's case, routed and booked with the doctor."""
        state = await self.cases.open(
            self.amma,
            self.dir.patient,
            UntrustedText(value="මට ඔලුව කැරකෙනවා", source="patient_message"),
            Language.SI,
        )
        await self.pump()
        async with self.uow_factory() as uow:
            uow.publish(
                AppointmentBooked(
                    appointment_id=AppointmentId(uuid4()),
                    practitioner_role_id=PractitionerRoleId(uuid4()),
                    patient_id=self.dir.patient,
                    case_id=state.id,
                ),
                aggregate_id="booking",
                patient_id=self.dir.patient,
            )
            await uow.commit()
        await self.pump()
        self.dir.booked = state.id
        return state.id

    async def asks_doctor(self, case_id: CaseId, text: str) -> None:
        await self.cases.add_patient_message(
            self.amma, case_id, UntrustedText(value=text, source="patient_question")
        )
        await self.pump()

    async def says(self, case_id: CaseId, text: str) -> None:
        await self.cases.add_patient_message(
            self.amma, case_id, UntrustedText(value=text, source="patient_message")
        )
        await self.pump()

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

    async def status(self, case_id: CaseId) -> CaseStatus:
        return (await self.cases.get(self.amma, case_id)).status

    def get(self, path: str, token: str = DOCTOR_TOKEN) -> Any:
        return self.http.get(path, headers={"Authorization": f"Bearer {token}"})

    def post(self, path: str, body: dict[str, Any], token: str = DOCTOR_TOKEN) -> Any:
        return self.http.post(path, json=body, headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def clinic(uow_factory: UnitOfWorkFactory, clock: FixedClock) -> Clinic:
    return Clinic(uow_factory, clock)


# ----- the brief --------------------------------------------------------------------------------


async def test_a_booking_builds_a_sourced_brief_for_the_doctor(clinic: Clinic) -> None:
    case_id = await clinic.booked_case()
    response = await clinic.get(f"/api/v1/doctor/cases/{case_id}/brief")
    assert response.status_code == 200
    brief = response.json()
    by_label = {line["label"]: line for line in brief["lines"]}
    assert by_label["Penicillin"]["provenance_ids"][0] == str(clinic.penicillin.id)
    assert by_label["Type 2 diabetes"]["section"] == BriefSection.CONDITIONS
    assert by_label["HbA1c"]["source"] == "report"
    assert all(line["verified"] for line in brief["lines"])
    assert "කැරකෙනවා" not in response.text  # the patient's chat never appears
    assert brief["missing_facts"] == ["home_glucose_reading"]
    doctor = [n.kind for n in clinic.notifications.items if n.user_id == clinic.dir.doctor]
    assert doctor == ["doctor_brief"]


async def test_another_doctor_cannot_see_the_brief(clinic: Clinic) -> None:
    case_id = await clinic.booked_case()
    response = await clinic.get(f"/api/v1/doctor/cases/{case_id}/brief", token="other-doctor")
    assert response.status_code == 404


async def test_the_missing_glucose_is_asked_and_comes_back_with_its_source(clinic: Clinic) -> None:
    case_id = await clinic.booked_case()
    question = (await clinic.replies())[-1]
    assert question.kind == "question"
    assert question.text.startswith("ඔබගේ වෛද්‍යවරයා අසයි")  # Amma's language
    await clinic.says(case_id, "210")
    brief = (await clinic.get(f"/api/v1/doctor/cases/{case_id}/brief")).json()
    line = next(x for x in brief["lines"] if x["label"] == "home glucose reading")
    assert (line["section"], line["source"], line["value"], line["verified"]) == (
        "results",
        "patient_reported",
        "210.0",
        True,
    )
    assert brief["missing_facts"] == []
    assert brief["version"] == 2


# ----- the reply: the doctor's words, approved ---------------------------------------------


async def _draft(clinic: Clinic, case_id: CaseId, text: str = NOTES) -> str:
    response = await clinic.post(f"/api/v1/doctor/cases/{case_id}/notes", {"text": text})
    assert response.status_code == 202
    await clinic.pump()
    pending = (await clinic.get("/api/v1/doctor/approvals")).json()
    assert len(pending) == 1
    return str(pending[0]["draft_id"])


async def test_the_doctors_words_reach_amma_only_after_approval(clinic: Clinic) -> None:
    case_id = await clinic.booked_case()
    draft_id = await _draft(clinic, case_id)
    assert await clinic.status(case_id) is CaseStatus.AWAITING_DOCTOR
    assert all(r.kind != "doctor_reply" for r in await clinic.replies())  # not yet
    pending = (await clinic.get("/api/v1/doctor/approvals")).json()
    assert pending[0]["text"] == NOTES  # unchanged: no model rewrote it
    response = await clinic.post(f"/api/v1/doctor/approvals/{draft_id}", {"decision": "approved"})
    assert response.status_code == 202
    await clinic.pump()
    reply = (await clinic.replies())[-1]
    assert (reply.kind, reply.text) == ("doctor_reply", NOTES)
    assert await clinic.status(case_id) is CaseStatus.REPLIED
    assert (await clinic.get("/api/v1/doctor/approvals")).json() == []
    async with clinic.uow_factory() as uow:
        reasons = (await uow.session.execute(select(AgentJobRow.error_code))).scalars().all()
    assert "awaiting_doctor" in reasons  # the graph paused at the approval interrupt
    assert "reply_approved" in reasons  # and the doctor's decision resumed it (not the fallback)


async def test_an_edited_reply_sends_the_edited_words(clinic: Clinic) -> None:
    case_id = await clinic.booked_case()
    draft_id = await _draft(clinic, case_id)
    edited = "Check your sugar each morning. Come on Monday at 9."
    await clinic.post(
        f"/api/v1/doctor/approvals/{draft_id}", {"decision": "edited", "text": edited}
    )
    await clinic.pump()
    assert (await clinic.replies())[-1].text == edited


async def test_a_rejected_reply_is_never_sent(clinic: Clinic) -> None:
    case_id = await clinic.booked_case()
    draft_id = await _draft(clinic, case_id)
    await clinic.post(f"/api/v1/doctor/approvals/{draft_id}", {"decision": "rejected"})
    await clinic.pump()
    assert all(r.kind != "doctor_reply" for r in await clinic.replies())
    async with clinic.uow_factory() as uow:
        status = (
            await uow.session.execute(
                select(AgentMessageRow.status).where(AgentMessageRow.intent == "reply_draft")
            )
        ).scalar_one()
    assert status == "returned"


async def test_a_verifier_refusal_stops_the_draft_before_the_doctor(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    clinic = Clinic(uow_factory, clock, verdict=VerdictKind.BLOCK)
    case_id = await clinic.booked_case()
    await clinic.post(f"/api/v1/doctor/cases/{case_id}/notes", {"text": NOTES})
    await clinic.pump()
    assert (await clinic.get("/api/v1/doctor/approvals")).json() == []
    assert await clinic.status(case_id) is CaseStatus.IN_CARE  # never asked for approval


async def test_after_four_hours_without_the_doctor_the_case_returns_to_care(clinic: Clinic) -> None:
    case_id = await clinic.booked_case()
    draft_id = await _draft(clinic, case_id)
    clinic.clock.advance(timedelta(hours=4, minutes=1))
    await clinic.pump()
    assert await clinic.status(case_id) is CaseStatus.IN_CARE
    late = await clinic.post(f"/api/v1/doctor/approvals/{draft_id}", {"decision": "approved"})
    assert late.status_code == 409  # the timed-out draft can no longer be approved
    assert all(r.kind != "doctor_reply" for r in await clinic.replies())


async def test_other_doctors_cannot_decide(clinic: Clinic) -> None:
    case_id = await clinic.booked_case()
    draft_id = await _draft(clinic, case_id)
    response = await clinic.post(
        f"/api/v1/doctor/approvals/{draft_id}", {"decision": "approved"}, token="other-doctor"
    )
    assert response.status_code == 404


# ----- "Ask my doctor" ----------------------------------------------------------------------------


async def _replied(clinic: Clinic) -> CaseId:
    case_id = await clinic.booked_case()
    draft_id = await _draft(clinic, case_id)
    await clinic.post(f"/api/v1/doctor/approvals/{draft_id}", {"decision": "approved"})
    await clinic.pump()
    return case_id


async def test_the_doctors_reply_offers_ask_my_doctor(clinic: Clinic) -> None:
    await _replied(clinic)
    reply = (await clinic.replies())[-1]
    assert [(c.action_kind, c.label) for c in reply.suggestions] == [
        ("ask_doctor", "වෛද්‍යවරයාගෙන් අහන්න")
    ]


async def test_a_question_reaches_the_doctor_as_a_link_not_as_text(clinic: Clinic) -> None:
    case_id = await _replied(clinic)
    question = "Can I take the tablet after dinner?"
    await clinic.asks_doctor(case_id, question)
    assert (await clinic.replies())[-1].text.startswith("මම ඔබේ ප්‍රශ්නය")
    response = await clinic.get(f"/api/v1/doctor/cases/{case_id}/brief")
    assert question not in response.text  # the brief links to it, never copies it
    line = next(x for x in response.json()["lines"] if x["label"] == "Patient question")
    assert line["section"] == "open_questions" and line["verified"]
    doctor = Principal(
        kind=PrincipalKind.USER,
        user_id=clinic.dir.doctor,
        role=Role.DOCTOR,
        acting_for=clinic.dir.doctor,
        purpose=Purpose.TREATMENT,
        trace_id=TraceId(TRACE),
    )
    timeline = (await clinic.cases.timeline(doctor, case_id, None, 100)).items
    asked = [i for i in timeline if i.params.get("to") == "doctor"]
    assert [i.params["text"] for i in asked] == [question]
    assert str(asked[0].params["communication_id"]) == line["provenance_ids"][0]


async def test_a_red_flag_in_a_question_takes_the_emergency_lane(clinic: Clinic) -> None:
    case_id = await _replied(clinic)
    await clinic.asks_doctor(case_id, "I have chest pain now, should I wait for you?")
    assert (await clinic.replies())[-1].kind == "emergency"
    brief = (await clinic.get(f"/api/v1/doctor/cases/{case_id}/brief")).json()
    assert all(x["label"] != "Patient question" for x in brief["lines"])  # not a routine question


async def test_without_a_booked_doctor_amma_is_told_to_book_first(clinic: Clinic) -> None:
    state = await clinic.cases.open(
        clinic.amma,
        clinic.dir.patient,
        UntrustedText(value="මට ඔලුව කැරකෙනවා", source="patient_message"),
        Language.SI,
    )
    await clinic.pump()
    await clinic.asks_doctor(state.id, "Is this serious?")
    reply = (await clinic.replies())[-1]
    assert reply.text.startswith("මේ සඳහා ඔබට තවම වෛද්‍යවරයෙකු නැහැ")
    assert [c.action_kind for c in reply.suggestions] == ["show_doctors"]
