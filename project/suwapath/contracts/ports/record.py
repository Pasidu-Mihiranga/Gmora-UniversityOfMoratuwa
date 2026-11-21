"""record: the FHIR-shaped record behind the access policy (SEC-11, CON-7 to CON-9)."""

from __future__ import annotations

from typing import Any, Protocol

from suwapath.contracts.common import Contract, Page
from suwapath.contracts.enums import Purpose
from suwapath.contracts.fhir.base import PatientResource
from suwapath.contracts.fhir.resources import (
    AllergyIntolerance,
    Condition,
    MedicationStatement,
    Observation,
    Patient,
    Provenance,
)
from suwapath.contracts.ids import PatientId, ResourceId, UserId
from suwapath.contracts.principal import Principal


class RecordSummary(Contract):
    """Only the parts the principal may see; hidden parts are absent, not empty-marked."""

    patient: Patient | None
    conditions: tuple[Condition, ...] = ()
    allergies: tuple[AllergyIntolerance, ...] = ()
    medications: tuple[MedicationStatement, ...] = ()
    recent_observations: tuple[Observation, ...] = ()
    hidden_categories_count: int = 0


class RecordService(Protocol):
    async def patient_for_user(self, user_id: UserId) -> PatientId | None: ...

    async def summary(
        self, principal: Principal, patient_id: PatientId, purpose: Purpose
    ) -> RecordSummary: ...

    async def list_resources(
        self,
        principal: Principal,
        patient_id: PatientId,
        resource_type: str,
        cursor: str | None,
        limit: int,
    ) -> Page[PatientResource]: ...

    async def get_resource(
        self, principal: Principal, resource_type: str, resource_id: ResourceId
    ) -> PatientResource:
        """Raises NotFound when missing or not visible (CON-11)."""
        ...

    async def write_resource(
        self, principal: Principal, resource: PatientResource
    ) -> PatientResource:
        """Creates or updates (optimistic on meta.version_id); emits record.resource_written."""
        ...

    async def get_provenance(
        self, principal: Principal, provenance_id: ResourceId
    ) -> Provenance: ...

    async def export_fhir(self, principal: Principal, patient_id: PatientId) -> dict[str, Any]:
        """FHIR R4 Bundle (type collection) of what the principal may see."""
        ...

    async def destroy_patient_key(self, principal: Principal, patient_id: PatientId) -> None:
        """Crypto-shredding (SEC-25)."""
        ...
