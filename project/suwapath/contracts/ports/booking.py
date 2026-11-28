"""booking: sessions, slots, holds, appointments (BOOK-1 to BOOK-7)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from suwapath.contracts.common import Contract, Page
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
from suwapath.contracts.principal import Principal


class SlotView(Contract):
    slot_id: SlotId
    schedule_id: ScheduleId
    practitioner_role_id: PractitionerRoleId
    number: int
    start: datetime
    free: bool


class Hold(Contract):
    id: HoldId
    slot_id: SlotId
    patient_id: PatientId
    case_id: CaseId | None
    expires_at: datetime


class SessionSummary(Contract):
    schedule_id: ScheduleId
    practitioner_role_id: PractitionerRoleId
    start: datetime
    end: datetime
    capacity: int
    booked: int


class HospitalAppointmentRow(Contract):
    """BOOK-7: no clinical data; sensitive appointments show the number only."""

    appointment_id: AppointmentId
    number: int
    patient_first_name: str | None
    status: str


class BookingService(Protocol):
    async def slots(
        self, practitioner_role_id: PractitionerRoleId, date_from: date, date_to: date
    ) -> list[SlotView]: ...

    async def hold(
        self, principal: Principal, slot_id: SlotId, case_id: CaseId | None, sensitive: bool = False
    ) -> Hold:
        """Raises SafetyStop for emergency cases (BOOK-5), SlotUnavailable on races (BOOK-3)."""
        ...

    async def confirm(
        self, principal: Principal, hold_id: HoldId, idempotency_key: str
    ) -> Appointment:
        """Person action only (BOOK-2); idempotent (BOOK-4)."""
        ...

    async def cancel(self, principal: Principal, appointment_id: AppointmentId) -> Appointment: ...

    async def appointments(
        self, principal: Principal, patient_id: PatientId, cursor: str | None, limit: int
    ) -> Page[Appointment]: ...

    async def release_holds_for_case(self, case_id: CaseId) -> int: ...

    async def practitioner_for_role(
        self, practitioner_role_id: PractitionerRoleId
    ) -> PractitionerId | None: ...

    async def booked_case(self, patient_id: PatientId) -> tuple[CaseId, PractitionerId] | None:
        """The patient's latest booked appointment that belongs to a case (IDs only)."""
        ...

    async def hospital_sessions(
        self, principal: Principal, organization_id: OrganizationId, day: date
    ) -> list[SessionSummary]: ...

    async def hospital_session_appointments(
        self, principal: Principal, schedule_id: ScheduleId
    ) -> list[HospitalAppointmentRow]: ...
