"""`BookingService` on Postgres (M08, BOOK-1 to BOOK-7).

- **Hold** a slot for 5 minutes (one live hold per slot; an expired hold is cleared by its timer or
  by the next hold). Agents may hold; **only the patient confirms** (BOOK-2, `write_irreversible`).
- **Confirm** is idempotent by `Idempotency-Key` (BOOK-4). A partial unique index allows one booked
  appointment per slot, so a race becomes "slot just taken" with the next free slot (BOOK-3).
- **Emergency:** a red flag releases the case's holds and stops booking for that case (BOOK-5).
- Booking a doctor starts the treating relationship in consent; cancelling ends it (SEC-10 rule 4).
- **Hospital view:** sessions with counts, and per session the appointment number and the
  patient's first name only, nothing clinical; sensitive bookings show the number only (BOOK-7).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from suwapath.contracts.common import Page
from suwapath.contracts.enums import AppointmentStatus, DataCategory, PrincipalKind, Role
from suwapath.contracts.errors import AccessDenied, NotFound, SafetyStop, SlotUnavailable
from suwapath.contracts.events import EventEnvelope
from suwapath.contracts.events.catalog import (
    AppointmentBooked,
    AppointmentCancelled,
    RedFlagRaised,
    SlotHeld,
    TimerFired,
)
from suwapath.contracts.fhir.base import Meta
from suwapath.contracts.fhir.resources import Appointment
from suwapath.contracts.ids import (
    AppointmentId,
    CaseId,
    HoldId,
    OrganizationId,
    PatientId,
    PractitionerId,
    PractitionerRoleId,
    ScheduleId,
    SlotId,
)
from suwapath.contracts.ports.booking import Hold, HospitalAppointmentRow, SessionSummary, SlotView
from suwapath.contracts.ports.consent import AccessRequest
from suwapath.contracts.principal import Principal
from suwapath.modules.booking.adapters.models import (
    AppointmentRow,
    HoldRow,
    PractitionerRoleRow,
    ScheduleRow,
    SlotRow,
    StoppedCaseRow,
)
from suwapath.platform.clock import COLOMBO
from suwapath.platform.ids import new_id
from suwapath.platform.models import IdempotencyKeyRow
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

HOLD_MINUTES = 5  # BOOK-2
HOLD_TIMER = "slot_hold_expiry"
CONFIRM_ROUTE = "booking.confirm"
Port = Callable[[], Any]


class SqlBookingService:
    def __init__(self, uow_factory: UnitOfWorkFactory, ports: Callable[[str], Any]) -> None:
        self._uow = uow_factory
        self._port = ports  # identity, consent, record, access_policy, patient_cipher; per call

    # ----- slots and holds ----------------------------------------------------------------------

    def today(self) -> date:
        """Today in Sri Lanka, from the Clock port (EVT-16)."""
        return self._uow.clock.now().astimezone(COLOMBO).date()

    async def slots(
        self, practitioner_role_id: PractitionerRoleId, date_from: date, date_to: date
    ) -> list[SlotView]:
        begin = datetime.combine(date_from, datetime.min.time(), tzinfo=COLOMBO)
        end = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=COLOMBO)
        async with self._uow.join() as uow:
            now = uow.clock.now()
            rows = (
                (
                    await uow.session.execute(
                        select(SlotRow)
                        .where(
                            SlotRow.practitioner_role_id == practitioner_role_id,
                            SlotRow.starts_at >= begin,
                            SlotRow.starts_at < end,
                        )
                        .order_by(SlotRow.starts_at)
                    )
                )
                .scalars()
                .all()
            )
            taken = await _taken(uow, [r.id for r in rows], now)
        return [
            SlotView(
                slot_id=SlotId(r.id),
                schedule_id=ScheduleId(r.schedule_id),
                practitioner_role_id=PractitionerRoleId(r.practitioner_role_id),
                number=r.number,
                start=r.starts_at,
                free=r.id not in taken and r.starts_at > now,
            )
            for r in rows
        ]

    async def hold(
        self, principal: Principal, slot_id: SlotId, case_id: CaseId | None, sensitive: bool = False
    ) -> Hold:
        patient = _patient_of(principal)
        async with self._uow.join() as uow:
            now = uow.clock.now()
            if case_id is not None and await uow.session.get(StoppedCaseRow, case_id) is not None:
                raise SafetyStop("Booking is stopped while this may be an emergency")
            slot = await uow.session.get(SlotRow, slot_id)
            if slot is None or slot.starts_at <= now:
                raise NotFound()
            if slot_id in await _taken(uow, [slot_id], now, holds=False):
                raise SlotUnavailable(
                    "That slot was just taken", next_slot_id=await _next_free(uow, slot, now)
                )
            await uow.session.execute(
                delete(HoldRow).where(HoldRow.slot_id == slot_id, HoldRow.expires_at <= now)
            )
            hold_id = HoldId(new_id())
            expires = now + timedelta(minutes=HOLD_MINUTES)
            created = (
                await uow.session.execute(
                    insert(HoldRow)
                    .values(
                        id=hold_id,
                        slot_id=slot_id,
                        patient_id=patient,
                        case_id=case_id,
                        sensitive=sensitive,
                        expires_at=expires,
                        created_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["slot_id"])
                    .returning(HoldRow.id)
                )
            ).scalar_one_or_none()
            if created is None:
                raise SlotUnavailable(
                    "That slot was just taken", next_slot_id=await _next_free(uow, slot, now)
                )
            await uow.schedule_timer(
                kind=HOLD_TIMER,
                due_at=expires,
                ref_type="hold",
                ref_id=str(hold_id),
                dedupe_key=f"hold:{hold_id}",
                payload={},
            )
            uow.publish(
                SlotHeld(hold_id=hold_id, slot_id=slot_id, expires_at=expires, case_id=case_id),
                aggregate_id=str(hold_id),
                patient_id=patient,
            )
        return Hold(
            id=hold_id, slot_id=slot_id, patient_id=patient, case_id=case_id, expires_at=expires
        )

    # ----- confirm and cancel -------------------------------------------------------------------

    async def confirm(
        self, principal: Principal, hold_id: HoldId, idempotency_key: str
    ) -> Appointment:
        """Only the patient themselves confirms (BOOK-2); the same key returns the same booking."""
        if (
            principal.kind is not PrincipalKind.USER
            or principal.role is not Role.PATIENT
            or principal.user_id is None
        ):
            raise AccessDenied("Only the patient can confirm a booking")
        patient = _patient_of(principal)
        key = (idempotency_key or str(hold_id))[:80]
        async with self._uow.join() as uow:
            done = await uow.session.get(IdempotencyKeyRow, (principal.user_id, CONFIRM_ROUTE, key))
            if done is not None:
                return await self._appointment(
                    uow, AppointmentId(UUID(done.response["appointment_id"]))
                )
            now = uow.clock.now()
            hold = await uow.session.get(HoldRow, hold_id)
            if hold is None or hold.patient_id != patient or hold.expires_at <= now:
                raise NotFound()  # expired, someone else's, or already used
            if (
                hold.case_id is not None
                and await uow.session.get(StoppedCaseRow, hold.case_id) is not None
            ):
                raise SafetyStop("Booking is stopped while this may be an emergency")
            slot = await uow.session.get(SlotRow, hold.slot_id)
            role = (
                await uow.session.get(PractitionerRoleRow, slot.practitioner_role_id)
                if slot
                else None
            )
            if slot is None or role is None:
                raise NotFound()
            appointment_id = AppointmentId(new_id())
            try:
                async with uow.session.begin_nested():
                    uow.session.add(
                        AppointmentRow(
                            id=appointment_id,
                            patient_id=patient,
                            slot_id=slot.id,
                            practitioner_role_id=role.id,
                            organization_id=role.organization_id,
                            starts_at=slot.starts_at,
                            status=AppointmentStatus.BOOKED.value,
                            case_id=hold.case_id,
                            sensitive=hold.sensitive,
                            first_name_enc=await self._first_name(
                                principal, patient, appointment_id
                            ),
                        )
                    )
                    await uow.session.flush()
            except IntegrityError:
                raise SlotUnavailable(
                    "That slot was just taken", next_slot_id=await _next_free(uow, slot, now)
                ) from None
            deleted = (
                await uow.session.execute(
                    delete(HoldRow).where(HoldRow.id == hold_id).returning(HoldRow.id)
                )
            ).scalar_one_or_none()
            if deleted is None:
                raise NotFound()  # a parallel confirm used this hold first
            await uow.cancel_timer(f"hold:{hold_id}")
            uow.session.add(
                IdempotencyKeyRow(
                    user_id=principal.user_id,
                    route=CONFIRM_ROUTE,
                    key=key,
                    request_hash=str(hold_id),
                    status_code=201,
                    response={"appointment_id": str(appointment_id)},
                    created_at=now,
                )
            )
            await uow.session.flush()
            uow.publish(
                AppointmentBooked(
                    appointment_id=appointment_id,
                    practitioner_role_id=PractitionerRoleId(role.id),
                    patient_id=patient,
                    case_id=CaseId(hold.case_id) if hold.case_id else None,
                    sensitive=hold.sensitive,
                ),
                aggregate_id=str(appointment_id),
                patient_id=patient,
            )
            await self._start_treatment(
                PractitionerId(role.practitioner_id), patient, appointment_id, now, slot.starts_at
            )
            return await self._appointment(uow, appointment_id)

    async def cancel(self, principal: Principal, appointment_id: AppointmentId) -> Appointment:
        async with self._uow.join() as uow:
            row = await uow.session.get(AppointmentRow, appointment_id)
            if row is None:
                raise NotFound()
            by = await self._canceller(principal, row)
            if row.status == AppointmentStatus.BOOKED.value:
                row.status = AppointmentStatus.CANCELLED.value
                row.updated_at = uow.clock.now()
                await uow.session.flush()
                uow.publish(
                    AppointmentCancelled(
                        appointment_id=appointment_id,
                        by=by,
                        patient_id=PatientId(row.patient_id),
                        case_id=CaseId(row.case_id) if row.case_id else None,
                    ),
                    aggregate_id=str(appointment_id),
                    patient_id=PatientId(row.patient_id),
                )
                consent = self._port("consent")
                if consent is not None:
                    await consent.end_treatment(appointment_id)
            return await self._appointment(uow, appointment_id)

    async def appointments(
        self, principal: Principal, patient_id: PatientId, cursor: str | None, limit: int
    ) -> Page[Appointment]:
        await self._may_read(principal, patient_id)
        limit = max(1, min(limit, 50))
        async with self._uow.join() as uow:
            query = (
                select(AppointmentRow)
                .where(AppointmentRow.patient_id == patient_id)
                .order_by(AppointmentRow.starts_at.desc(), AppointmentRow.id)
                .limit(limit + 1)
            )
            if cursor:
                query = query.where(AppointmentRow.starts_at < datetime.fromisoformat(cursor))
            rows = list((await uow.session.execute(query)).scalars())
            items = [_resource(r) for r in rows[:limit]]
        return Page(
            items=items,
            next_cursor=rows[limit - 1].starts_at.isoformat() if len(rows) > limit else None,
        )

    # ----- emergency (BOOK-5, SAF-10) -----------------------------------------------------------

    async def release_holds_for_case(self, case_id: CaseId) -> int:
        """Stops booking for the case and releases its holds. Idempotent."""
        async with self._uow.join() as uow:
            await uow.session.execute(
                insert(StoppedCaseRow)
                .values(case_id=case_id, stopped_at=uow.clock.now())
                .on_conflict_do_nothing()
            )
            released = (
                (
                    await uow.session.execute(
                        delete(HoldRow).where(HoldRow.case_id == case_id).returning(HoldRow.id)
                    )
                )
                .scalars()
                .all()
            )
            for hold_id in released:
                await uow.cancel_timer(f"hold:{hold_id}")
            return len(released)

    async def on_red_flag(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, RedFlagRaised)
        await self.release_holds_for_case(payload.case_id)

    async def on_timer(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        payload = envelope.payload
        assert isinstance(payload, TimerFired)
        if payload.kind == HOLD_TIMER:
            await uow.session.execute(
                delete(HoldRow).where(
                    HoldRow.id == UUID(payload.ref_id), HoldRow.expires_at <= uow.clock.now()
                )
            )

    # ----- hospital view (BOOK-7) ---------------------------------------------------------------

    async def hospital_sessions(
        self, principal: Principal, organization_id: OrganizationId, day: date
    ) -> list[SessionSummary]:
        await self._hospital_admin(principal, organization_id)
        begin = datetime.combine(day, datetime.min.time(), tzinfo=COLOMBO)
        async with self._uow.join() as uow:
            booked = (
                select(func.count())
                .where(
                    AppointmentRow.slot_id == SlotRow.id,
                    AppointmentRow.status == AppointmentStatus.BOOKED.value,
                )
                .correlate(SlotRow)
                .scalar_subquery()
            )
            counts = (
                select(SlotRow.schedule_id, func.sum(booked).label("booked"))
                .group_by(SlotRow.schedule_id)
                .subquery()
            )
            rows = await uow.session.execute(
                select(ScheduleRow, func.coalesce(counts.c.booked, 0))
                .outerjoin(counts, counts.c.schedule_id == ScheduleRow.id)
                .where(
                    ScheduleRow.organization_id == organization_id,
                    ScheduleRow.starts_at >= begin,
                    ScheduleRow.starts_at < begin + timedelta(days=1),
                )
                .order_by(ScheduleRow.starts_at)
            )
            return [
                SessionSummary(
                    schedule_id=ScheduleId(s.id),
                    practitioner_role_id=PractitionerRoleId(s.practitioner_role_id),
                    start=s.starts_at,
                    end=s.ends_at,
                    capacity=s.capacity,
                    booked=int(n),
                )
                for s, n in rows.all()
            ]

    async def hospital_session_appointments(
        self, principal: Principal, schedule_id: ScheduleId
    ) -> list[HospitalAppointmentRow]:
        async with self._uow.join() as uow:
            schedule = await uow.session.get(ScheduleRow, schedule_id)
            if schedule is None:
                raise NotFound()
            await self._hospital_admin(principal, OrganizationId(schedule.organization_id))
            rows = (
                await uow.session.execute(
                    select(AppointmentRow, SlotRow.number)
                    .join(SlotRow, SlotRow.id == AppointmentRow.slot_id)
                    .where(SlotRow.schedule_id == schedule_id)
                    .order_by(SlotRow.number)
                )
            ).all()
        out = []
        for row, number in rows:
            first = None
            if not row.sensitive and row.first_name_enc:
                cipher = self._port("patient_cipher")
                try:
                    first = (
                        (
                            await cipher.decrypt(
                                PatientId(row.patient_id),
                                "booking_appointments",
                                "first_name_enc",
                                row.id,
                                row.first_name_enc,
                            )
                        ).decode()
                        if cipher
                        else None
                    )
                except NotFound:
                    first = None  # erased patient
            out.append(
                HospitalAppointmentRow(
                    appointment_id=AppointmentId(row.id),
                    number=number,
                    patient_first_name=first,
                    status=row.status,
                )
            )
        return out

    # ----- internals ----------------------------------------------------------------------------

    async def _appointment(self, uow: UnitOfWork, appointment_id: AppointmentId) -> Appointment:
        row = await uow.session.get(AppointmentRow, appointment_id)
        if row is None:
            raise NotFound()
        return _resource(row)

    async def _first_name(
        self, principal: Principal, patient: PatientId, appointment_id: AppointmentId
    ) -> bytes | None:
        record, cipher = self._port("record"), self._port("patient_cipher")
        if record is None or cipher is None:
            return None
        try:
            demographics = await record.get_resource(principal, "Patient", patient)
        except NotFound:
            return None
        given = demographics.name.given[0] if demographics.name.given else demographics.name.text
        if not given:
            return None
        sealed: bytes = await cipher.encrypt(
            patient, "booking_appointments", "first_name_enc", appointment_id, given.encode()
        )
        return sealed

    async def _start_treatment(
        self,
        practitioner: PractitionerId,
        patient: PatientId,
        appointment: AppointmentId,
        booked_at: datetime,
        at: datetime,
    ) -> None:
        identity, consent = self._port("identity"), self._port("consent")
        if identity is None or consent is None:
            return
        doctor = await identity.user_for_practitioner(practitioner)
        if doctor is not None:
            await consent.record_treatment(
                patient_id=patient,
                doctor_id=doctor.id,
                appointment_id=appointment,
                booked_at=booked_at,
                appointment_at=at,
            )

    async def _canceller(self, principal: Principal, row: AppointmentRow) -> str:
        if (
            principal.kind is PrincipalKind.USER
            and principal.role is Role.PATIENT
            and principal.patient_scope == row.patient_id
        ):
            return "patient"
        if principal.kind is PrincipalKind.USER and principal.role is Role.HOSPITAL_ADMIN:
            await self._hospital_admin(principal, OrganizationId(row.organization_id))
            return "hospital"
        raise NotFound()

    async def _hospital_admin(self, principal: Principal, organization_id: OrganizationId) -> None:
        identity = self._port("identity")
        if (
            principal.kind is not PrincipalKind.USER
            or principal.role is not Role.HOSPITAL_ADMIN
            or identity is None
            or principal.user_id is None
        ):
            raise NotFound()
        account = await identity.get_user(principal.user_id)
        if account.organization_id != organization_id:
            raise NotFound()  # another hospital's sessions look like none

    async def _may_read(self, principal: Principal, patient_id: PatientId) -> None:
        policy = self._port("access_policy")
        decision = None
        if policy is not None:
            decision = await policy.decide(
                AccessRequest(
                    principal=principal,
                    action="read",
                    patient_id=patient_id,
                    category=DataCategory.APPOINTMENTS,
                    resource_type="Appointment",
                    purpose=principal.purpose,
                )
            )
        if decision is None or not decision.allowed:
            raise NotFound()


def _patient_of(principal: Principal) -> PatientId:
    """The patient a booking is for: the patient, or the agent acting for them."""
    if principal.patient_scope is None or principal.kind is PrincipalKind.SYSTEM:
        raise AccessDenied()
    if principal.kind is PrincipalKind.USER and principal.role is not Role.PATIENT:
        raise AccessDenied()
    return principal.patient_scope


async def _taken(
    uow: UnitOfWork, slot_ids: list[Any], now: datetime, holds: bool = True
) -> set[Any]:
    if not slot_ids:
        return set()
    booked = await uow.session.execute(
        select(AppointmentRow.slot_id).where(
            AppointmentRow.slot_id.in_(slot_ids),
            AppointmentRow.status == AppointmentStatus.BOOKED.value,
        )
    )
    taken = set(booked.scalars())
    if holds:
        held = await uow.session.execute(
            select(HoldRow.slot_id).where(HoldRow.slot_id.in_(slot_ids), HoldRow.expires_at > now)
        )
        taken |= set(held.scalars())
    return taken


async def _next_free(uow: UnitOfWork, slot: SlotRow, now: datetime) -> str | None:
    """BOOK-3: the next free slot of the same doctor, offered with "slot just taken"."""
    candidates = (
        (
            await uow.session.execute(
                select(SlotRow.id)
                .where(
                    SlotRow.practitioner_role_id == slot.practitioner_role_id,
                    SlotRow.starts_at > max(now, slot.starts_at - timedelta(seconds=1)),
                    SlotRow.id != slot.id,
                )
                .order_by(SlotRow.starts_at)
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    taken = await _taken(uow, list(candidates), now)
    return next((str(c) for c in candidates if c not in taken), None)


def _resource(row: AppointmentRow) -> Appointment:
    return Appointment(
        id=AppointmentId(row.id),
        meta=Meta(last_updated=row.updated_at, source="booking"),
        patient_id=PatientId(row.patient_id),
        slot_id=SlotId(row.slot_id),
        practitioner_role_id=PractitionerRoleId(row.practitioner_role_id),
        organization_id=OrganizationId(row.organization_id),
        start=row.starts_at,
        status=AppointmentStatus(row.status),
        case_id=CaseId(row.case_id) if row.case_id else None,
        sensitive=row.sensitive,
    )
