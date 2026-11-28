"""Record routes (spec 10, M05). Thin: the record service asks the access policy on every read."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from suwapath.contracts.common import Page
from suwapath.contracts.enums import Purpose, Role
from suwapath.contracts.fhir.base import PatientResource
from suwapath.contracts.fhir.resources import Provenance
from suwapath.contracts.ids import PatientId, ResourceId
from suwapath.contracts.ports.record import RecordSummary
from suwapath.contracts.principal import Principal
from suwapath.modules.record.application.service import KINDS, SqlRecordService
from suwapath.platform.http.auth import require

PURPOSES = {Role.GUARDIAN: Purpose.GUARDIAN_MONITORING, Role.DOCTOR: Purpose.TREATMENT}
Viewer = Annotated[
    Principal, Depends(require(Role.PATIENT, Role.GUARDIAN, Role.DOCTOR, purposes=PURPOSES))
]
Patient = Annotated[Principal, Depends(require(Role.PATIENT))]
Doctor = Annotated[Principal, Depends(require(Role.DOCTOR, purpose=Purpose.TREATMENT))]
PatientPath = Annotated[PatientId, Path()]
TYPE_PATTERN = "^(" + "|".join(KINDS) + ")$"


def build_router(service: SqlRecordService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["record"])

    @router.get(
        "/patients/{patient_id}/summary",
        summary="Record summary filtered by access",
        operation_id="record_get_patients_patient_id_summary",
    )
    async def summary(patient_id: PatientPath, principal: Viewer) -> RecordSummary:
        return await service.summary(principal, patient_id, principal.purpose)

    @router.get(
        "/patients/{patient_id}/resources",
        summary="Resources of one type",
        operation_id="record_get_patients_patient_id_resources",
    )
    async def resources(
        patient_id: PatientPath,
        principal: Viewer,
        type: Annotated[str, Query(pattern=TYPE_PATTERN)],  # noqa: A002  (the spec's query name)
        cursor: Annotated[str | None, Query(max_length=40)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> Page[PatientResource]:
        return await service.list_resources(principal, patient_id, type, cursor, limit)

    @router.get(
        "/patients/{patient_id}/fhir/export",
        summary="FHIR R4 Bundle",
        operation_id="record_get_patients_patient_id_fhir_export",
    )
    async def export(patient_id: PatientPath, principal: Patient) -> dict[str, Any]:
        return await service.export_fhir(principal, patient_id)

    @router.get(
        "/doctor/sources/{provenance_id}",
        summary="Source behind a brief line",
        operation_id="record_get_doctor_sources_provenance_id",
    )
    async def source(provenance_id: Annotated[ResourceId, Path()], principal: Doctor) -> Provenance:
        return await service.get_provenance(principal, provenance_id)

    return router
