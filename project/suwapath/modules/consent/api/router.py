"""Consent routes (spec 10, M03). The grants are real; plain-language rules and settings are C01
and stay stubs until then."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path
from pydantic import Field

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import DataCategory, Role
from suwapath.contracts.fhir.resources import Consent
from suwapath.contracts.ids import PatientId, UserId
from suwapath.contracts.principal import Principal
from suwapath.modules.consent.application.service import SqlConsentService
from suwapath.platform.http.auth import require
from suwapath.platform.http.stubs import StubRoute, stub_router

Patient = Annotated[Principal, Depends(require(Role.PATIENT))]
PatientPath = Annotated[PatientId, Path()]
GranteePath = Annotated[UserId, Path()]


class GrantChange(Contract):
    categories: frozenset[DataCategory] = Field(max_length=len(DataCategory))
    emergency_alert: bool = True  # SEC-10 rule 7: the emergency alert to this family member


def build_router(service: SqlConsentService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["consent"])

    @router.get(
        "/patients/{patient_id}/consents",
        summary="Grants per guardian and doctor",
        operation_id="consent_get_patients_patient_id_consents",
    )
    async def grants(patient_id: PatientPath, principal: Patient) -> list[Consent]:
        return await service.grants_for_patient(principal, patient_id)

    @router.put(
        "/patients/{patient_id}/consents/{grantee_id}",
        summary="Set what a person may see",
        operation_id="consent_put_patients_patient_id_consents_grantee_id",
    )
    async def set_grant(
        patient_id: PatientPath,
        grantee_id: GranteePath,
        body: GrantChange,
        principal: Patient,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=80)
        ] = None,
    ) -> Consent:
        return await service.set_grant(
            principal,
            patient_id,
            grantee_id,
            body.categories,
            idempotency_key or "",
            emergency_alert=body.emergency_alert,
        )

    @router.delete(
        "/patients/{patient_id}/consents/{grantee_id}",
        status_code=204,
        summary="Revoke access",
        operation_id="consent_delete_patients_patient_id_consents_grantee_id",
    )
    async def revoke(patient_id: PatientPath, grantee_id: GranteePath, principal: Patient) -> None:
        await service.revoke_grant(principal, patient_id, grantee_id)

    return router


ROUTES = [
    StubRoute(
        "POST",
        "/rules/parse",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Plain-language rule to a proposal",
        "M03",
    ),
    StubRoute(
        "POST", "/rules", (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR), "Confirm a rule", "M03"
    ),
    StubRoute("GET", "/rules", (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR), "My rules", "M03"),
    StubRoute(
        "GET",
        "/settings",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "My assistant settings",
        "M03",
    ),
    StubRoute(
        "PATCH", "/settings", (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR), "Change settings", "M03"
    ),
    StubRoute(
        "POST", "/settings/suggestions", (Role.GUARDIAN,), "Suggest a setting to a patient", "M03"
    ),
    StubRoute(
        "POST",
        "/settings/suggestions/{suggestion_id}",
        (Role.PATIENT,),
        "Accept or decline a suggestion",
        "M03",
    ),
]

stubs = stub_router("consent", ROUTES)
