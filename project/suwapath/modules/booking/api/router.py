"""Booking routes (spec 10, M08). Thin: the booking service decides who may do what."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query

from suwapath.contracts.common import Contract, Page
from suwapath.contracts.enums import Purpose, Role
from suwapath.contracts.errors import NotFound
from suwapath.contracts.fhir.resources import Appointment
from suwapath.contracts.ids import (
    AppointmentId,
    CaseId,
    HoldId,
    PractitionerRoleId,
    ScheduleId,
    SlotId,
)
from suwapath.contracts.ports.booking import Hold, HospitalAppointmentRow, SessionSummary, SlotView
from suwapath.contracts.principal import Principal
from suwapath.modules.booking.application.service import SqlBookingService
from suwapath.platform.http.auth import require

Patient = Annotated[Principal, Depends(require(Role.PATIENT, purpose=Purpose.BOOKING))]
PatientOrGuardian = Annotated[
    Principal,
    Depends(
        require(Role.PATIENT, Role.GUARDIAN, purposes={Role.GUARDIAN: Purpose.GUARDIAN_MONITORING})
    ),
]
Canceller = Annotated[
    Principal,
    Depends(
        require(
            Role.PATIENT,
            Role.HOSPITAL_ADMIN,
            purposes={Role.HOSPITAL_ADMIN: Purpose.ADMINISTRATION},
        )
    ),
]
Admin = Annotated[Principal, Depends(require(Role.HOSPITAL_ADMIN, purpose=Purpose.ADMINISTRATION))]


class HoldRequest(Contract):
    slot_id: SlotId
    case_id: CaseId | None = None


def build_router(service: SqlBookingService, identity: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["booking"])

    @router.get(
        "/doctors/{practitioner_role_id}/slots",
        summary="Free slots",
        operation_id="booking_get_doctors_practitioner_role_id_slots",
    )
    async def slots(
        practitioner_role_id: Annotated[PractitionerRoleId, Path()],
        _: PatientOrGuardian,
        date_from: Annotated[date | None, Query(alias="from")] = None,
        days: Annotated[int, Query(ge=1, le=14)] = 7,
    ) -> list[SlotView]:
        start = date_from or service.today()
        return [
            s
            for s in await service.slots(
                practitioner_role_id, start, start + timedelta(days=days - 1)
            )
            if s.free
        ]

    @router.post(
        "/bookings/holds",
        status_code=201,
        summary="Hold a slot",
        operation_id="booking_post_bookings_holds",
    )
    async def hold(body: HoldRequest, principal: Patient) -> Hold:
        return await service.hold(principal, body.slot_id, body.case_id)

    @router.post(
        "/bookings/holds/{hold_id}/confirm",
        summary="Confirm a booking",
        operation_id="booking_post_bookings_holds_hold_id_confirm",
    )
    async def confirm(
        hold_id: Annotated[HoldId, Path()],
        principal: Patient,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=80)
        ] = None,
    ) -> Appointment:
        return await service.confirm(principal, hold_id, idempotency_key or "")

    @router.delete(
        "/bookings/{appointment_id}",
        summary="Cancel",
        operation_id="booking_delete_bookings_appointment_id",
    )
    async def cancel(
        appointment_id: Annotated[AppointmentId, Path()], principal: Canceller
    ) -> Appointment:
        return await service.cancel(principal, appointment_id)

    @router.get("/bookings", summary="My appointments", operation_id="booking_get_bookings")
    async def mine(
        principal: Patient,
        cursor: Annotated[str | None, Query(max_length=40)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> Page[Appointment]:
        if principal.patient_scope is None:
            raise NotFound()
        return await service.appointments(principal, principal.patient_scope, cursor, limit)

    @router.get(
        "/hospital/sessions",
        summary="Sessions with counts",
        operation_id="booking_get_hospital_sessions",
    )
    async def sessions(
        principal: Admin, day: Annotated[date | None, Query(alias="date")] = None
    ) -> list[SessionSummary]:
        id_service = identity()
        if id_service is None or principal.user_id is None:
            raise NotFound()
        account = await id_service.get_user(principal.user_id)
        if account.organization_id is None:
            raise NotFound()
        return await service.hospital_sessions(
            principal, account.organization_id, day or service.today()
        )

    @router.get(
        "/hospital/sessions/{schedule_id}/appointments",
        summary="Session appointments",
        operation_id="booking_get_hospital_sessions_schedule_id_appointments",
    )
    async def session_appointments(
        schedule_id: Annotated[ScheduleId, Path()], principal: Admin
    ) -> list[HospitalAppointmentRow]:
        return await service.hospital_session_appointments(principal, schedule_id)

    return router
