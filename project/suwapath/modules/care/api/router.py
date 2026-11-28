"""Care routes (spec 10, M09): doses, adherence and guardian alert rules. Programmes, offerings
and referrals (P01 to P03) stay stubs until then."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query

from suwapath.contracts.enums import Purpose, Role
from suwapath.contracts.ids import PatientId
from suwapath.contracts.ports.care import AdherenceSummary, DoseItem
from suwapath.contracts.principal import Principal
from suwapath.modules.care.application.service import (
    AlertRule,
    RuleRequest,
    SqlCareService,
    parse_dose_id,
)
from suwapath.platform.http.auth import require
from suwapath.platform.http.stubs import ALL_ROLES, StubRoute, stub_router

PURPOSES = {Role.GUARDIAN: Purpose.GUARDIAN_MONITORING, Role.DOCTOR: Purpose.TREATMENT}
Patient = Annotated[Principal, Depends(require(Role.PATIENT))]
PatientOrGuardian = Annotated[
    Principal, Depends(require(Role.PATIENT, Role.GUARDIAN, purposes=PURPOSES))
]
Viewer = Annotated[
    Principal, Depends(require(Role.PATIENT, Role.GUARDIAN, Role.DOCTOR, purposes=PURPOSES))
]
Guardian = Annotated[
    Principal, Depends(require(Role.GUARDIAN, purpose=Purpose.GUARDIAN_MONITORING))
]
PatientPath = Annotated[PatientId, Path()]


def build_router(service: SqlCareService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["care"])

    @router.get(
        "/patients/{patient_id}/doses",
        summary="Doses of one day",
        operation_id="care_get_patients_patient_id_doses",
    )
    async def doses(
        patient_id: PatientPath,
        principal: PatientOrGuardian,
        day: Annotated[date | None, Query(alias="date")] = None,
    ) -> list[DoseItem]:
        return await service.doses_for_day(principal, patient_id, day or service.today())

    @router.post(
        "/patients/{patient_id}/doses/{dose_id}/taken",
        summary="Mark a dose taken",
        operation_id="care_post_patients_patient_id_doses_dose_id_taken",
    )
    async def taken(
        patient_id: PatientPath,
        dose_id: Annotated[str, Path(max_length=200)],
        principal: Patient,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=80)
        ] = None,
    ) -> DoseItem:
        statement_id, scheduled_at = parse_dose_id(dose_id)
        return await service.mark_taken(
            principal, patient_id, statement_id, scheduled_at, idempotency_key or ""
        )

    @router.get(
        "/patients/{patient_id}/adherence",
        summary="Adherence summary",
        operation_id="care_get_patients_patient_id_adherence",
    )
    async def adherence(
        patient_id: PatientPath, principal: Viewer, days: Annotated[int, Query(ge=1, le=31)] = 7
    ) -> AdherenceSummary:
        return await service.adherence(principal, patient_id, days)

    @router.get("/guardian/rules", summary="My alert rules", operation_id="care_get_guardian_rules")
    async def rules(principal: Guardian) -> list[AlertRule]:
        return await service.rules_for(principal)

    @router.post(
        "/guardian/rules",
        summary="Create or change an alert rule",
        operation_id="care_post_guardian_rules",
    )
    async def create_rule(body: RuleRequest, principal: Guardian) -> AlertRule:
        return await service.create_rule(principal, body)

    return router


ROUTES = [
    StubRoute("GET", "/programmes", ALL_ROLES, "Programme catalogue", "M09"),
    StubRoute(
        "GET",
        "/programmes/{programme_key}/offerings",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Approved offerings nearby",
        "M09",
    ),
    StubRoute(
        "POST", "/patients/{patient_id}/enrollments", (Role.PATIENT,), "Join a programme", "M09"
    ),
    StubRoute(
        "GET",
        "/patients/{patient_id}/enrollments",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Enrolments",
        "M09",
    ),
    StubRoute("POST", "/doctor/referrals", (Role.DOCTOR,), "Refer a patient to a programme", "M09"),
    StubRoute("GET", "/patients/{patient_id}/referrals", (Role.PATIENT,), "My referrals", "M09"),
    StubRoute(
        "POST", "/referrals/{referral_id}/accept", (Role.PATIENT,), "Accept a referral", "M09"
    ),
    StubRoute(
        "POST", "/referrals/{referral_id}/decline", (Role.PATIENT,), "Decline a referral", "M09"
    ),
    StubRoute(
        "GET", "/hospital/offerings", (Role.HOSPITAL_ADMIN,), "My hospital's offerings", "M09"
    ),
    StubRoute("POST", "/hospital/offerings", (Role.HOSPITAL_ADMIN,), "Create an offering", "M09"),
    StubRoute(
        "PATCH",
        "/hospital/offerings/{offering_id}",
        (Role.HOSPITAL_ADMIN,),
        "Edit or submit an offering",
        "M09",
    ),
    StubRoute("GET", "/admin/programmes", (Role.SYSTEM_ADMIN,), "Programme catalogue admin", "M09"),
    StubRoute(
        "PUT",
        "/admin/programmes/{programme_key}",
        (Role.SYSTEM_ADMIN,),
        "Enable or disable a programme",
        "M09",
    ),
    StubRoute(
        "GET", "/admin/offerings", (Role.SYSTEM_ADMIN,), "Offerings waiting for approval", "M09"
    ),
    StubRoute(
        "POST",
        "/admin/offerings/{offering_id}/decision",
        (Role.SYSTEM_ADMIN,),
        "Approve or reject",
        "M09",
    ),
    StubRoute("GET", "/guardian/dependents", (Role.GUARDIAN,), "Linked patients", "M09"),
    StubRoute("GET", "/guardian/alerts", (Role.GUARDIAN,), "Alerts", "M09"),
    StubRoute(
        "POST", "/guardian/alerts/{alert_id}/ack", (Role.GUARDIAN,), "Acknowledge an alert", "M09"
    ),
]

stubs = stub_router("care", ROUTES)
