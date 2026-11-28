"""Critical tier: booking on Postgres (BOOK-1 to BOOK-7). Holds, person-only idempotent confirms,
no double booking under 20 parallel confirms, the emergency stop, treating relationships, and the
hospital view with first names only."""

from __future__ import annotations

import asyncio
import base64
import os
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import func, select

from suwapath.contracts.enums import AgentKind, DataCategory, Language, PrincipalKind, Purpose, Role
from suwapath.contracts.errors import NotFound, SafetyStop, SlotUnavailable
from suwapath.contracts.events import EventEnvelope
from suwapath.contracts.events.catalog import RedFlagRaised
from suwapath.contracts.fhir.base import HumanName, Meta
from suwapath.contracts.fhir.resources import Patient
from suwapath.contracts.ids import (
    CaseId,
    EventId,
    OrganizationId,
    PatientId,
    PractitionerId,
    ResourceId,
    TraceId,
)
from suwapath.contracts.ports.consent import AccessRequest
from suwapath.contracts.ports.identity import UserAccount
from suwapath.contracts.principal import Principal
from suwapath.contracts.testing.suites import BookingContract
from suwapath.modules.booking.adapters.models import (
    AppointmentRow,
    OrganizationRow,
    PractitionerRoleRow,
    ScheduleRow,
    SlotRow,
)
from suwapath.modules.booking.application.service import SqlBookingService
from suwapath.modules.consent.application.service import SqlAccessPolicy, SqlConsentService
from suwapath.modules.identity.application.service import SqlIdentityService
from suwapath.modules.record.application.keys import SqlPatientCipher
from suwapath.modules.record.application.service import SqlRecordService
from suwapath.platform.clock import FixedClock
from suwapath.platform.crypto import DekCache, LocalKeyProvider
from suwapath.platform.ids import new_id
from suwapath.platform.jobs.timers import fire_due_timers
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
TRACE = TraceId("b" * 32)


# ----- the shared contract suite, against the real service ---------------------------------------


class TestSqlBooking(BookingContract):
    @pytest.fixture
    def booking_world(
        self, uow_factory: UnitOfWorkFactory, database_url: str
    ) -> tuple[SqlBookingService, Any]:
        conninfo = database_url.replace("postgresql+psycopg://", "postgresql://")

        def add_slot(role_id: Any, start: datetime) -> Any:
            org, schedule, slot = new_id(), new_id(), new_id()
            with psycopg.connect(conninfo, autocommit=True) as conn:
                conn.execute(
                    "INSERT INTO booking_organizations (id, name, district, lat, lng)"
                    " VALUES (%s, 'H', 'D', 7.0, 80.0)",
                    (org,),
                )
                conn.execute(
                    "INSERT INTO booking_practitioner_roles"
                    " (id, practitioner_id, organization_id, specialty, fee_lkr)"
                    " VALUES (%s, %s, %s, 'general_medicine', 1000)",
                    (role_id, new_id(), org),
                )
                conn.execute(
                    "INSERT INTO booking_schedules"
                    " (id, practitioner_role_id, organization_id, starts_at, ends_at, capacity)"
                    " VALUES (%s, %s, %s, %s, %s, 10)",
                    (schedule, role_id, org, start, start + timedelta(hours=2)),
                )
                conn.execute(
                    "INSERT INTO booking_slots"
                    " (id, schedule_id, practitioner_role_id, number, starts_at)"
                    " VALUES (%s, %s, %s, 1, %s)",
                    (slot, schedule, role_id, start),
                )
            return slot

        return SqlBookingService(uow_factory, lambda name: None), add_slot


# ----- a hospital, a doctor, Amma ----------------------------------------------------------------


class Hospital:
    async def setup(self, uow_factory: UnitOfWorkFactory, clock: FixedClock) -> Hospital:
        self.uow_factory, self.clock = uow_factory, clock
        self.identity = SqlIdentityService(uow_factory, "s" * 40)
        self.org, self.other_org = OrganizationId(new_id()), OrganizationId(new_id())
        self.practitioner = PractitionerId(new_id())
        self.patient = PatientId(uuid4())
        self.amma = await self._user("amma@example.test", Role.PATIENT, patient_id=self.patient)
        self.doctor = await self._user(
            "dr@example.test", Role.DOCTOR, practitioner_id=self.practitioner
        )
        self.admin = await self._user(
            "admin@lotus.test", Role.HOSPITAL_ADMIN, organization_id=self.org
        )
        self.other_admin = await self._user(
            "admin@other.test", Role.HOSPITAL_ADMIN, organization_id=self.other_org
        )
        self.policy = SqlAccessPolicy(
            uow_factory,
            lambda: self.identity,
            lambda: {AgentKind.PATIENT_COMPANION.value: frozenset(DataCategory)},
        )
        self.consent = SqlConsentService(uow_factory, lambda: self.identity)
        provider = LocalKeyProvider(base64.b64encode(os.urandom(32)).decode())
        cipher = SqlPatientCipher(uow_factory, lambda: provider, DekCache())
        record = SqlRecordService(uow_factory, lambda: self.policy, lambda: cipher)
        await record.import_resources(
            [
                Patient(
                    id=ResourceId(self.patient),
                    meta=Meta(last_updated=clock.now(), source="seed"),
                    patient_id=self.patient,
                    name=HumanName(given=["Kamala"], family="Perera"),
                    birth_date=datetime(1954, 3, 2).date(),
                    age_band="60-74",
                    gender="female",
                    language=Language.SI,
                )
            ],
            agent="seed",
        )
        ports = {
            "identity": self.identity,
            "consent": self.consent,
            "record": record,
            "access_policy": self.policy,
            "patient_cipher": cipher,
        }
        self.booking = SqlBookingService(uow_factory, ports.get)
        self.role, self.schedule, self.slots = new_id(), new_id(), [new_id() for _ in range(3)]
        start = clock.now() + timedelta(days=1)
        async with uow_factory() as uow:
            uow.session.add(
                OrganizationRow(
                    id=self.org, name="Lotus Care", district="Kurunegala", lat=7.48, lng=80.36
                )
            )
            uow.session.add(
                PractitionerRoleRow(
                    id=self.role,
                    practitioner_id=self.practitioner,
                    organization_id=self.org,
                    specialty="general_medicine",
                    fee_lkr=2500,
                )
            )
            uow.session.add(
                ScheduleRow(
                    id=self.schedule,
                    practitioner_role_id=self.role,
                    organization_id=self.org,
                    starts_at=start,
                    ends_at=start + timedelta(hours=2),
                    capacity=3,
                )
            )
            for n, slot in enumerate(self.slots, start=1):
                uow.session.add(
                    SlotRow(
                        id=slot,
                        schedule_id=self.schedule,
                        practitioner_role_id=self.role,
                        number=n,
                        starts_at=start + timedelta(minutes=10 * n),
                    )
                )
            await uow.commit()
        return self

    async def _user(self, login: str, role: Role, **extra: Any) -> UserAccount:
        return await self.identity.create_user(
            login=login,
            password="long enough",
            role=role,
            display_name=login,
            language=Language.SI,
            **extra,
        )

    def as_user(self, account: UserAccount, purpose: Purpose = Purpose.BOOKING) -> Principal:
        return Principal(
            kind=PrincipalKind.USER,
            user_id=account.id,
            role=account.role,
            acting_for=account.id,
            patient_scope=account.patient_id,
            purpose=purpose,
            trace_id=TRACE,
        )

    @property
    def amma_view(self) -> Principal:
        return self.as_user(self.amma)

    async def booked(self) -> int:
        async with self.uow_factory() as uow:
            return int(
                (
                    await uow.session.execute(
                        select(func.count())
                        .select_from(AppointmentRow)
                        .where(AppointmentRow.status == "booked")
                    )
                ).scalar_one()
            )


@pytest.fixture
async def hospital(uow_factory: UnitOfWorkFactory, clock: FixedClock) -> Hospital:
    return await Hospital().setup(uow_factory, clock)


# ----- holds and confirms ------------------------------------------------------------------------


async def test_twenty_parallel_confirms_make_one_appointment(hospital: Hospital) -> None:
    hold = await hospital.booking.hold(hospital.amma_view, hospital.slots[0], None)
    results = await asyncio.gather(
        *(hospital.booking.confirm(hospital.amma_view, hold.id, f"key-{n}") for n in range(20)),
        return_exceptions=True,
    )
    made = [r for r in results if not isinstance(r, BaseException)]
    assert len(made) == 1 and await hospital.booked() == 1
    assert all(
        isinstance(r, (NotFound, SlotUnavailable)) for r in results if isinstance(r, BaseException)
    )


async def test_the_same_key_returns_the_same_appointment(hospital: Hospital) -> None:
    hold = await hospital.booking.hold(hospital.amma_view, hospital.slots[0], None)
    first = await hospital.booking.confirm(hospital.amma_view, hold.id, "k1")
    again = await hospital.booking.confirm(hospital.amma_view, hold.id, "k1")
    assert first.id == again.id and await hospital.booked() == 1


async def test_a_taken_slot_offers_the_next_free_one(hospital: Hospital) -> None:
    other = await hospital._user("x@example.test", Role.PATIENT, patient_id=PatientId(uuid4()))
    await hospital.booking.hold(hospital.amma_view, hospital.slots[0], None)
    with pytest.raises(SlotUnavailable) as taken:
        await hospital.booking.hold(hospital.as_user(other), hospital.slots[0], None)
    assert taken.value.details["next_slot_id"] == str(hospital.slots[1])


async def test_an_expired_hold_frees_the_slot_and_cannot_be_confirmed(hospital: Hospital) -> None:
    hold = await hospital.booking.hold(hospital.amma_view, hospital.slots[0], None)
    hospital.clock.advance(timedelta(minutes=5, seconds=1))
    with pytest.raises(NotFound):
        await hospital.booking.confirm(hospital.amma_view, hold.id, "late")
    await fire_due_timers(hospital.uow_factory)
    free = await hospital.booking.slots(
        hospital.role, hospital.booking.today(), hospital.booking.today() + timedelta(days=2)
    )
    assert free[0].free


async def test_an_agent_holds_but_never_confirms(hospital: Hospital) -> None:
    companion = Principal(
        kind=PrincipalKind.AGENT,
        agent_kind=AgentKind.PATIENT_COMPANION,
        role=Role.PATIENT,
        acting_for=hospital.amma.id,
        patient_scope=hospital.patient,
        purpose=Purpose.BOOKING,
        trace_id=TRACE,
    )
    hold = await hospital.booking.hold(companion, hospital.slots[0], None)
    with pytest.raises(Exception, match="Only the patient"):
        await hospital.booking.confirm(companion, hold.id, "k")
    assert (
        await hospital.booking.confirm(hospital.amma_view, hold.id, "k")
    ).status.value == "booked"


# ----- the emergency stop (BOOK-5) ---------------------------------------------------------------


async def test_a_red_flag_releases_holds_and_stops_booking_for_the_case(hospital: Hospital) -> None:
    case = CaseId(uuid4())
    hold = await hospital.booking.hold(hospital.amma_view, hospital.slots[0], case)
    envelope = EventEnvelope.wrap(
        RedFlagRaised(
            case_id=case,
            patient_id=hospital.patient,
            rule_ids=("RF-CARD-001",),
            urgency="emergency",
        ),
        event_id=EventId(uuid4()),
        occurred_at=hospital.clock.now(),
        aggregate_id="c",
        trace_id=TRACE,
    )
    async with hospital.uow_factory() as uow:
        await hospital.booking.on_red_flag(envelope, uow)
        await uow.commit()
    with pytest.raises(NotFound):  # the hold is gone
        await hospital.booking.confirm(hospital.amma_view, hold.id, "k")
    with pytest.raises(SafetyStop):
        await hospital.booking.hold(hospital.amma_view, hospital.slots[1], case)
    assert (
        await hospital.booking.hold(hospital.amma_view, hospital.slots[1], None)
    ).slot_id == hospital.slots[1]


# ----- treating relationships (SEC-10 rule 4) ----------------------------------------------------


async def test_booking_lets_the_doctor_treat_and_cancelling_ends_it(hospital: Hospital) -> None:
    doctor = hospital.as_user(hospital.doctor, Purpose.TREATMENT)

    async def may_read() -> bool:
        decision = await hospital.policy.decide(
            AccessRequest(
                principal=doctor,
                action="read",
                patient_id=hospital.patient,
                category=DataCategory.CONDITIONS,
                resource_type="T",
                purpose=Purpose.TREATMENT,
            )
        )
        return decision.allowed

    assert not await may_read()
    hold = await hospital.booking.hold(hospital.amma_view, hospital.slots[0], None)
    appointment = await hospital.booking.confirm(hospital.amma_view, hold.id, "k")
    assert await may_read()
    cancelled = await hospital.booking.cancel(hospital.amma_view, appointment.id)
    assert cancelled.status.value == "cancelled" and not await may_read()


# ----- the hospital view (BOOK-7) ----------------------------------------------------------------


async def test_the_hospital_sees_counts_and_first_names_only(hospital: Hospital) -> None:
    hold = await hospital.booking.hold(hospital.amma_view, hospital.slots[1], None)
    await hospital.booking.confirm(hospital.amma_view, hold.id, "k")
    admin = hospital.as_user(hospital.admin, Purpose.ADMINISTRATION)
    tomorrow = hospital.booking.today() + timedelta(days=1)
    sessions = await hospital.booking.hospital_sessions(admin, hospital.org, tomorrow)
    assert [(s.capacity, s.booked) for s in sessions] == [(3, 1)]
    rows = await hospital.booking.hospital_session_appointments(admin, sessions[0].schedule_id)
    assert [(r.number, r.patient_first_name, r.status) for r in rows] == [(2, "Kamala", "booked")]
    other = hospital.as_user(hospital.other_admin, Purpose.ADMINISTRATION)
    with pytest.raises(NotFound):
        await hospital.booking.hospital_session_appointments(other, sessions[0].schedule_id)
    with pytest.raises(NotFound):
        await hospital.booking.hospital_sessions(other, hospital.org, tomorrow)


async def test_the_patient_lists_their_appointments_and_the_hospital_can_cancel(
    hospital: Hospital,
) -> None:
    hold = await hospital.booking.hold(hospital.amma_view, hospital.slots[0], None)
    made = await hospital.booking.confirm(hospital.amma_view, hold.id, "k")
    page = await hospital.booking.appointments(
        hospital.as_user(hospital.amma, Purpose.SELF_ACCESS), hospital.patient, None, 10
    )
    assert [a.id for a in page.items] == [made.id]
    admin = hospital.as_user(hospital.admin, Purpose.ADMINISTRATION)
    assert (await hospital.booking.cancel(admin, made.id)).status.value == "cancelled"
    other = hospital.as_user(hospital.other_admin, Purpose.ADMINISTRATION)
    with pytest.raises(NotFound):
        await hospital.booking.cancel(other, made.id)
