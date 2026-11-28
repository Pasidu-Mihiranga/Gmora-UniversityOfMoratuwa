"""Critical tier: the case service on Postgres (CON-10, CON-11, DB-6, EVT-1, EVT-2, invariant 6)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from suwapath.contracts.cases import CaseEventDraft, CaseEventKind
from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import (
    AgentKind,
    CaseStatus,
    DataCategory,
    Language,
    PrincipalKind,
    Purpose,
    Role,
)
from suwapath.contracts.errors import AccessDenied, NotFound, VersionConflict
from suwapath.contracts.events import EventEnvelope
from suwapath.contracts.events.catalog import AppointmentBooked, TimerFired
from suwapath.contracts.ids import (
    AppointmentId,
    CaseId,
    EventId,
    PatientId,
    PractitionerRoleId,
    TimerId,
    TraceId,
    UserId,
)
from suwapath.contracts.ports.identity import UserAccount
from suwapath.contracts.principal import Principal
from suwapath.contracts.testing.doubles_core import InMemoryAccessPolicy
from suwapath.contracts.testing.suites import CaseServiceContract
from suwapath.modules.cases.adapters.models import CaseEventRow
from suwapath.modules.cases.api.router import build_router
from suwapath.modules.cases.application.service import APPROVAL_TIMER, SqlCaseService
from suwapath.modules.cases.application.subscribers import CaseSubscribers
from suwapath.platform.clock import FixedClock
from suwapath.platform.config import Settings
from suwapath.platform.http.app import create_app
from suwapath.platform.models import OutboxRow, TimerRow
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
TRACE = TraceId("0" * 31 + "1")
K = CaseEventKind


class OwnRecordPolicy(InMemoryAccessPolicy):
    """The double, plus: any patient principal owns the patient in its scope (contract suite)."""

    def _human_allows(self, human: UserId, role: Role, request: Any) -> tuple[bool, str]:
        principal = request.principal
        if role is Role.PATIENT and principal.patient_scope == request.patient_id:
            return True, "own_record"
        return super()._human_allows(human, role, request)


def _service(uow_factory: UnitOfWorkFactory, policy: InMemoryAccessPolicy) -> SqlCaseService:
    return SqlCaseService(uow_factory, lambda: policy)


class TestSqlCases(CaseServiceContract):
    @pytest.fixture
    def cases(self, uow_factory: UnitOfWorkFactory) -> SqlCaseService:
        return _service(uow_factory, OwnRecordPolicy())


# ----- a small world: Amma, her son, her doctor ------------------------------------------------


class World:
    def __init__(self) -> None:
        self.patient_user = UserId(uuid4())
        self.patient = PatientId(uuid4())
        self.son = UserId(uuid4())
        self.doctor = UserId(uuid4())
        self.policy = InMemoryAccessPolicy(
            patients_by_user={self.patient_user: self.patient},
            guardian_grants={(self.son, self.patient): frozenset({DataCategory.ADHERENCE})},
            treating={(self.doctor, self.patient)},
            user_roles={
                self.patient_user: Role.PATIENT,
                self.son: Role.GUARDIAN,
                self.doctor: Role.DOCTOR,
            },
            agent_scopes={
                AgentKind.GUARDIAN_AGENT.value: frozenset({DataCategory.ADHERENCE}),
                AgentKind.PATIENT_COMPANION.value: frozenset(DataCategory),
            },
        )

    def user(self, user_id: UserId, role: Role, purpose: Purpose) -> Principal:
        return Principal(
            kind=PrincipalKind.USER,
            user_id=user_id,
            role=role,
            acting_for=user_id,
            patient_scope=self.patient if role is Role.PATIENT else None,
            purpose=purpose,
            trace_id=TRACE,
        )

    @property
    def amma(self) -> Principal:
        return self.user(self.patient_user, Role.PATIENT, Purpose.CARE_NAVIGATION)

    @property
    def son_view(self) -> Principal:
        return self.user(self.son, Role.GUARDIAN, Purpose.GUARDIAN_MONITORING)

    @property
    def doctor_view(self) -> Principal:
        return self.user(self.doctor, Role.DOCTOR, Purpose.TREATMENT)

    def agent(self, kind: AgentKind, acting_for: UserId, purpose: Purpose) -> Principal:
        return Principal(
            kind=PrincipalKind.AGENT,
            agent_kind=kind,
            acting_for=acting_for,
            patient_scope=self.patient,
            purpose=purpose,
            trace_id=TRACE,
        )

    @property
    def companion(self) -> Principal:
        return self.agent(AgentKind.PATIENT_COMPANION, self.patient_user, Purpose.CARE_NAVIGATION)

    @property
    def guardian_agent(self) -> Principal:
        return self.agent(AgentKind.GUARDIAN_AGENT, self.son, Purpose.GUARDIAN_MONITORING)


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def service(uow_factory: UnitOfWorkFactory, world: World) -> SqlCaseService:
    return _service(uow_factory, world.policy)


def _text(value: str) -> UntrustedText:
    return UntrustedText(value=value, source="patient_message")


async def _count(uow_factory: UnitOfWorkFactory, model: type, *where: Any) -> int:
    async with uow_factory() as uow:
        query = select(func.count()).select_from(model).where(*where)
        return int((await uow.session.execute(query)).scalar_one())


async def _to_awaiting_doctor(service: SqlCaseService, world: World) -> tuple[CaseId, str]:
    state = await service.open(world.amma, world.patient, _text("chest feels tight"), Language.SI)
    draft = str(uuid4())
    drafts = [
        CaseEventDraft(kind=K.INTAKE_STARTED, actor="patient_companion"),
        CaseEventDraft(kind=K.URGENCY_ASSESSED, data={"urgency": "soon_72h"}, actor="safety"),
        CaseEventDraft(
            kind=K.APPOINTMENT_BOOKED, data={"appointment_id": str(uuid4())}, actor="booking"
        ),
        CaseEventDraft(
            kind=K.DOCTOR_APPROVAL_REQUESTED,
            data={"draft_id": draft, "practitioner_id": str(uuid4())},
            actor="doctor_assistant",
        ),
    ]
    state = await service.append(world.companion, state.id, drafts, state.version)
    assert state.status is CaseStatus.AWAITING_DOCTOR
    return state.id, draft


# ----- open, messages, events in the same transaction ------------------------------------------


async def test_opening_with_a_message_records_it_and_tells_the_companion(
    service: SqlCaseService, world: World, uow_factory: UnitOfWorkFactory
) -> None:
    state = await service.open(world.amma, world.patient, _text("I feel dizzy"), Language.SI)
    assert state.version == 1  # the first message rides on case_opened
    async with uow_factory() as uow:
        types = (await uow.session.execute(select(OutboxRow.type))).scalars().all()
    assert types == ["cases.case_opened"]  # spec 03 section 5: this wakes the companion
    text = await service.latest_patient_text(state.id)
    assert text is not None and text.value == "I feel dizzy"


async def test_case_events_roll_back_with_the_caller(
    service: SqlCaseService, world: World, uow_factory: UnitOfWorkFactory
) -> None:
    async with uow_factory():
        await service.open(world.amma, world.patient, _text("hello"), Language.EN)
        # the caller fails before commit
    assert await _count(uow_factory, CaseEventRow) == 0
    assert await _count(uow_factory, OutboxRow) == 0


async def test_only_a_patient_opens_a_case_for_themselves(
    service: SqlCaseService, world: World
) -> None:
    with pytest.raises(AccessDenied):
        await service.open(world.son_view, world.patient, _text("x"), Language.EN)
    stranger = world.user(UserId(uuid4()), Role.PATIENT, Purpose.CARE_NAVIGATION)
    with pytest.raises(NotFound):
        await service.open(stranger, world.patient, _text("x"), Language.EN)


async def test_guardian_agent_opens_a_dose_case_with_a_code(
    service: SqlCaseService, world: World
) -> None:
    state = await service.open(
        world.guardian_agent,
        world.patient,
        UntrustedText(value="missed_doses", source="system"),
        Language.SI,
    )
    assert state.status is CaseStatus.OPEN
    seen = await service.get(world.son_view, state.id)
    assert seen.id == state.id


# ----- optimistic locking ----------------------------------------------------------------------


async def test_racing_appends_produce_one_conflict(service: SqlCaseService, world: World) -> None:
    state = await service.open(world.amma, world.patient, _text("x"), Language.EN)
    draft = [CaseEventDraft(kind=K.INTAKE_STARTED, actor="patient_companion")]
    results = await asyncio.gather(
        service.append(world.companion, state.id, draft, state.version),
        service.append(world.companion, state.id, draft, state.version),
        return_exceptions=True,
    )
    conflicts = [r for r in results if isinstance(r, VersionConflict)]
    assert len(conflicts) == 1
    assert (await service.get(world.amma, state.id)).version == state.version + 1


async def test_patient_messages_retry_through_a_conflict(
    service: SqlCaseService, world: World
) -> None:
    state = await service.open(world.amma, world.patient, _text("one"), Language.EN)
    await asyncio.gather(
        service.add_patient_message(world.amma, state.id, _text("two")),
        service.add_patient_message(world.amma, state.id, _text("three")),
    )
    assert (await service.get(world.amma, state.id)).version == state.version + 2


# ----- access ----------------------------------------------------------------------------------


async def test_guardian_sees_adherence_items_but_never_the_chat(
    service: SqlCaseService, world: World
) -> None:
    state = await service.open(world.amma, world.patient, _text("my chest hurts"), Language.SI)
    with pytest.raises(NotFound):
        await service.get(world.son_view, state.id)  # chat only: existence hidden (CON-11)
    state = await service.append(
        world.companion,
        state.id,
        [
            CaseEventDraft(
                kind=K.GUARDIAN_UPDATED,
                data={"status": "alerted", "last_alert_level": "concern"},
                actor="guardian_agent",
            ),
            CaseEventDraft(
                kind=K.FACT_RECORDED, data={"key": "chest_pain"}, actor="patient_companion"
            ),
        ],
        state.version,
    )
    seen = await service.get(world.son_view, state.id)
    assert seen.guardian_status is not None
    assert seen.facts == ()
    page = await service.timeline(world.son_view, state.id, None, 50)
    assert [i.summary_key for i in page.items] == ["case.guardian_updated"]
    full = await service.timeline(world.amma, state.id, None, 50)
    chat = [i for i in full.items if i.kind == "patient_message"]
    assert chat[0].params["text"] == "my chest hurts"
    assert all("text" not in i.params for i in page.items)


async def test_doctor_sees_the_case_of_a_treated_patient_only(
    service: SqlCaseService, world: World
) -> None:
    state = await service.open(world.amma, world.patient, _text("x"), Language.EN)
    assert (await service.get(world.doctor_view, state.id)).id == state.id
    other = world.user(UserId(uuid4()), Role.DOCTOR, Purpose.TREATMENT)
    with pytest.raises(NotFound):
        await service.get(other, state.id)


async def test_guardians_cannot_write(service: SqlCaseService, world: World) -> None:
    state = await service.open(world.amma, world.patient, _text("x"), Language.EN)
    with pytest.raises(AccessDenied):
        await service.append(
            world.son_view,
            state.id,
            [CaseEventDraft(kind=K.INTAKE_STARTED, actor="guardian")],
            state.version,
        )


async def test_unknown_case_is_not_found(service: SqlCaseService, world: World) -> None:
    with pytest.raises(NotFound):
        await service.get(world.amma, CaseId(uuid4()))


async def test_timeline_pages_in_order(service: SqlCaseService, world: World) -> None:
    state = await service.open(world.amma, world.patient, _text("1"), Language.EN)
    for n in range(2, 6):
        await service.add_patient_message(world.amma, state.id, _text(str(n)))
    first = await service.timeline(world.amma, state.id, None, 3)
    assert first.next_cursor is not None
    rest = await service.timeline(world.amma, state.id, first.next_cursor, 10)
    texts = [i.params.get("text") for i in first.items + rest.items if i.kind == "patient_message"]
    assert texts == ["1", "2", "3", "4", "5"]
    assert rest.next_cursor is None


# ----- doctor approval (invariant 6) and its timer ---------------------------------------------


async def test_only_a_doctor_records_the_decision(service: SqlCaseService, world: World) -> None:
    case_id, draft = await _to_awaiting_doctor(service, world)
    version = (await service.get(world.amma, case_id)).version
    decision = [
        CaseEventDraft(
            kind=K.DOCTOR_DECISION_RECORDED,
            data={"draft_id": draft, "decision": "approved"},
            actor="doctor",
        )
    ]
    for principal in (world.companion, world.amma):
        with pytest.raises(AccessDenied):
            await service.append(principal, case_id, decision, version)
    state = await service.append(world.doctor_view, case_id, decision, version)
    assert state.pending_approval is None
    with pytest.raises(AccessDenied):  # a doctor records only decisions
        await service.append(
            world.doctor_view,
            case_id,
            [CaseEventDraft(kind=K.HANDED_TO_HUMAN, data={"reason": "agent_failure"}, actor="d")],
            state.version,
        )


async def test_approval_request_sets_a_timer_and_the_decision_cancels_it(
    service: SqlCaseService, world: World, uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    case_id, draft = await _to_awaiting_doctor(service, world)
    async with uow_factory() as uow:
        due_at, status = (
            await uow.session.execute(
                select(TimerRow.due_at, TimerRow.status).where(TimerRow.kind == APPROVAL_TIMER)
            )
        ).one()
    assert due_at == clock.now() + timedelta(hours=4)
    assert status == "scheduled"
    version = (await service.get(world.amma, case_id)).version
    await service.append(
        world.doctor_view,
        case_id,
        [
            CaseEventDraft(
                kind=K.DOCTOR_DECISION_RECORDED,
                data={"draft_id": draft, "decision": "approved"},
                actor="doctor",
            )
        ],
        version,
    )
    assert await _count(uow_factory, TimerRow, TimerRow.status == "cancelled") == 1


async def _deliver(uow_factory: UnitOfWorkFactory, handler: Any, payload: Any, clock: Any) -> None:
    envelope = EventEnvelope.wrap(
        payload,
        event_id=EventId(uuid4()),
        occurred_at=clock.now(),
        aggregate_id="t",
        trace_id=TRACE,
    )
    async with uow_factory() as uow:
        await handler(envelope, uow)
        await uow.commit()


async def test_approval_timeout_returns_the_case_to_care(
    service: SqlCaseService, world: World, uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    case_id, _ = await _to_awaiting_doctor(service, world)
    subscribers = CaseSubscribers(service, uow_factory)
    fired = TimerFired(
        timer_id=TimerId(uuid4()), kind=APPROVAL_TIMER, ref_type="case", ref_id=str(case_id)
    )
    await _deliver(uow_factory, subscribers.on_timer, fired, clock)
    assert (await service.get(world.amma, case_id)).status is CaseStatus.IN_CARE
    await _deliver(uow_factory, subscribers.on_timer, fired, clock)  # a replay changes nothing
    assert (await service.get(world.amma, case_id)).status is CaseStatus.IN_CARE


async def test_booking_during_an_emergency_is_refused_and_skipped(
    service: SqlCaseService, world: World, uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    state = await service.open(world.amma, world.patient, _text("x"), Language.EN)
    state = await service.append(
        world.companion,
        state.id,
        [CaseEventDraft(kind=K.RED_FLAG_RAISED, data={"rule_ids": ["RF-1"]}, actor="safety")],
        state.version,
    )
    booked = AppointmentBooked(
        appointment_id=AppointmentId(uuid4()),
        practitioner_role_id=PractitionerRoleId(uuid4()),
        patient_id=world.patient,
        case_id=state.id,
    )
    subscribers = CaseSubscribers(service, uow_factory)
    await _deliver(uow_factory, subscribers.on_appointment_booked, booked, clock)
    after = await service.get(world.amma, state.id)
    assert after.status is CaseStatus.EMERGENCY
    assert after.appointments == ()


# ----- routes (thin) ---------------------------------------------------------------------------


async def test_routes_open_and_read_a_case(service: SqlCaseService, world: World) -> None:
    account = UserAccount(
        id=world.patient_user,
        role=Role.PATIENT,
        display_name="Amma",
        language=Language.SI,
        patient_id=world.patient,
    )

    async def verifier(token: str) -> UserAccount:
        assert token == "amma"
        return account

    app = create_app(
        Settings(_env_file=None), token_verifier=verifier, routers=[build_router(service)]
    )
    headers = {"Authorization": "Bearer amma"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        opened = await client.post(
            "/api/v1/cases", json={"text": "I feel dizzy", "language": "si"}, headers=headers
        )
        assert opened.status_code == 201
        case_id = opened.json()["id"]
        sent = await client.post(
            f"/api/v1/cases/{case_id}/messages", json={"text": "since morning"}, headers=headers
        )
        assert sent.status_code == 202
        timeline = (await client.get(f"/api/v1/cases/{case_id}/timeline", headers=headers)).json()
        assert [i["params"].get("text") for i in timeline["items"] if "text" in i["params"]] == [
            "I feel dizzy",
            "since morning",
        ]
        mine = (await client.get("/api/v1/cases", headers=headers)).json()
        assert [c["id"] for c in mine["items"]] == [case_id]
        missing = await client.get(f"/api/v1/cases/{uuid4()}", headers=headers)
        assert missing.status_code == 404
