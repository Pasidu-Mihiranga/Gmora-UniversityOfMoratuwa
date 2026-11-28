"""record: the FHIR-shaped record behind the access policy (SEC-11, CON-7 to CON-9)."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from suwapath.contracts.common import Contract, Page
from suwapath.contracts.enums import Language, Purpose
from suwapath.contracts.fhir.base import GeoPoint, PatientResource
from suwapath.contracts.fhir.resources import (
    AllergyIntolerance,
    Condition,
    DoseTiming,
    MedicationStatement,
    Observation,
    Patient,
    Provenance,
)
from suwapath.contracts.ids import PatientId, PractitionerId, ResourceId, UserId
from suwapath.contracts.principal import Principal


class RecordSummary(Contract):
    """Only the parts the principal may see; hidden parts are absent, not empty-marked."""

    patient: Patient | None
    conditions: tuple[Condition, ...] = ()
    allergies: tuple[AllergyIntolerance, ...] = ()
    medications: tuple[MedicationStatement, ...] = ()
    recent_observations: tuple[Observation, ...] = ()
    hidden_categories_count: int = 0


class MedicationSchedule(Contract):
    """When a medicine is due: timing only, no name or dose (care schedules doses from this)."""

    statement_id: ResourceId
    timing: DoseTiming


class PatientBasics(Contract):
    """Only what a patient's own agents need: safety rules (age, sex, conditions), the emergency
    alert (first name) and distance to doctors (home). No names beyond the first, no NIC."""

    first_name: str
    age_years: int | None = None
    sex: Literal["female", "male", "other", "unknown"] = "unknown"
    language: Language
    home: GeoPoint | None = None
    condition_codes: frozenset[str] = frozenset()


class PractitionerCard(Contract):
    """Directory data (no PHI): what a doctor card shows (NAV-7)."""

    practitioner_id: PractitionerId
    display_name: str
    languages: tuple[Language, ...] = ()


class RecordService(Protocol):
    async def patient_for_user(self, user_id: UserId) -> PatientId | None: ...

    async def practitioner_cards(
        self, practitioner_ids: list[PractitionerId]
    ) -> list[PractitionerCard]: ...

    async def search_practitioners(self, name: str, limit: int = 10) -> list[PractitionerId]:
        """Trigram match on the doctor's name, best first (direct channelling, NAV-13)."""
        ...

    async def patient_basics(self, patient_id: PatientId) -> PatientBasics | None:
        """For the agent runtime acting for this patient (the agent directory)."""
        ...

    async def medication_schedules(self, patient_id: PatientId) -> list[MedicationSchedule]:
        """Active, scheduled medicines (care, as a module). Timing only (CARE-2)."""
        ...

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
