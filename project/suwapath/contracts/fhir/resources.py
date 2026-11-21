"""FHIR R4-shaped resources (docs/specs/02-contracts-and-domain.md section 4)."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import ClassVar, Literal

from pydantic import Field

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import (
    AppointmentStatus,
    DataCategory,
    DoseSlot,
    Language,
    Specialty,
)
from suwapath.contracts.fhir.base import (
    Address,
    AgeBand,
    CodeableConcept,
    ContactPoint,
    GeoPoint,
    HumanName,
    PatientResource,
    Period,
    Quantity,
    Reference,
    Resource,
)
from suwapath.contracts.ids import (
    CaseId,
    DocumentId,
    OrganizationId,
    PractitionerId,
    PractitionerRoleId,
    ResourceId,
    ScheduleId,
    SlotId,
    UserId,
)

# ----- People and directory -----------------------------------------------------------------


class Patient(PatientResource):
    resource_type: ClassVar[str] = "Patient"
    category: ClassVar[DataCategory | None] = DataCategory.DEMOGRAPHICS
    encrypted_fields: ClassVar[frozenset[str]] = frozenset(
        {"name", "birth_date", "telecom", "address", "nic"}
    )

    user_id: UserId | None = None
    name: HumanName
    birth_date: date
    age_band: AgeBand  # stored plain for queries; derived from birth_date
    gender: Literal["female", "male", "other", "unknown"]
    language: Language
    telecom: list[ContactPoint] = Field(default_factory=list)
    address: Address | None = None
    nic: str | None = None
    district: str | None = None
    location: GeoPoint | None = None  # approximate home location for distance only


class RelatedPerson(PatientResource):
    """A guardian linked to a patient. Access is decided by Consent, not by this link."""

    resource_type: ClassVar[str] = "RelatedPerson"
    category: ClassVar[DataCategory | None] = DataCategory.CONTACT
    encrypted_fields: ClassVar[frozenset[str]] = frozenset({"name", "telecom"})

    user_id: UserId
    relationship: Literal[
        "son", "daughter", "spouse", "partner", "parent", "sibling", "carer", "other"
    ]
    name: HumanName
    telecom: list[ContactPoint] = Field(default_factory=list)
    lives_abroad: bool = False


class Practitioner(Resource):
    resource_type: ClassVar[str] = "Practitioner"

    user_id: UserId | None = None
    name: HumanName
    qualifications: list[str] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)


class Organization(Resource):
    resource_type: ClassVar[str] = "Organization"

    name: str
    district: str
    location: GeoPoint
    phone: str | None = None


class PractitionerRole(Resource):
    resource_type: ClassVar[str] = "PractitionerRole"

    practitioner_id: PractitionerId
    organization_id: OrganizationId
    specialty: Specialty
    fee_lkr: int = Field(ge=0)


class Schedule(Resource):
    """A channelling session: one doctor, one hospital, one time window."""

    resource_type: ClassVar[str] = "Schedule"

    practitioner_role_id: PractitionerRoleId
    organization_id: OrganizationId
    planning_horizon: Period
    capacity: int = Field(gt=0)


class Slot(Resource):
    resource_type: ClassVar[str] = "Slot"

    schedule_id: ScheduleId
    number: int = Field(gt=0)  # appointment number in the session
    start: datetime  # estimated time
    status: Literal["free", "held", "busy"]


# ----- Clinical record ------------------------------------------------------------------------


class Condition(PatientResource):
    resource_type: ClassVar[str] = "Condition"
    category: ClassVar[DataCategory | None] = DataCategory.CONDITIONS
    encrypted_fields: ClassVar[frozenset[str]] = frozenset({"code", "note"})

    code: CodeableConcept
    clinical_status: Literal["active", "recurrence", "remission", "resolved"]
    onset: date | None = None
    recorded_by: Reference | None = None
    note: str | None = None


class AllergyIntolerance(PatientResource):
    resource_type: ClassVar[str] = "AllergyIntolerance"
    category: ClassVar[DataCategory | None] = DataCategory.ALLERGIES
    encrypted_fields: ClassVar[frozenset[str]] = frozenset({"code", "reaction"})

    code: CodeableConcept  # substance; `coding` may include the drug class
    reaction: str | None = None
    criticality: Literal["low", "high", "unable-to-assess"] = "unable-to-assess"


class DoseTiming(Contract):
    slots: list[DoseSlot] = Field(default_factory=list)
    times: list[time] = Field(default_factory=list)  # explicit times override slot defaults
    days_of_week: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    as_needed: bool = False


class Dosage(Contract):
    text: str | None = None  # as prescribed; shown to the patient only as written by the doctor
    timing: DoseTiming


class MedicationStatement(PatientResource):
    """What is prescribed (CON-8)."""

    resource_type: ClassVar[str] = "MedicationStatement"
    category: ClassVar[DataCategory | None] = DataCategory.MEDICATIONS
    encrypted_fields: ClassVar[frozenset[str]] = frozenset({"medication", "dosage"})

    medication: CodeableConcept
    dosage: Dosage
    status: Literal["active", "on-hold", "stopped", "completed"]
    effective: Period | None = None
    prescriber: Reference | None = None


class MedicationAdministration(PatientResource):
    """One actual dose event: taken (`completed`) or missed (`not-done`) (CON-8)."""

    resource_type: ClassVar[str] = "MedicationAdministration"
    category: ClassVar[DataCategory | None] = DataCategory.ADHERENCE
    encrypted_fields: ClassVar[frozenset[str]] = frozenset({"note"})

    medication_statement_id: ResourceId
    scheduled_at: datetime
    slot: DoseSlot | None = None
    status: Literal["completed", "not-done"]
    status_reason: Literal["missed", "refused", "unavailable"] | None = None
    recorded_at: datetime
    recorded_by: Literal["patient", "companion", "timer", "guardian", "doctor"]
    note: str | None = None


class ReferenceRange(Contract):
    low: float | None = None
    high: float | None = None
    text: str | None = None  # as printed on the report (REP-4)


class Observation(PatientResource):
    resource_type: ClassVar[str] = "Observation"
    category: ClassVar[DataCategory | None] = DataCategory.OBSERVATIONS
    encrypted_fields: ClassVar[frozenset[str]] = frozenset({"code", "value"})

    code: CodeableConcept  # LOINC where known
    value: Quantity | str
    reference_range: ReferenceRange | None = None
    interpretation: Literal["low", "normal", "high", "abnormal"] | None = None
    effective_at: datetime
    derived_from: Reference | None = None  # DiagnosticReport


class DiagnosticReport(PatientResource):
    resource_type: ClassVar[str] = "DiagnosticReport"
    category: ClassVar[DataCategory | None] = DataCategory.REPORTS
    encrypted_fields: ClassVar[frozenset[str]] = frozenset({"conclusion_text"})

    report_category: Literal["lab", "imaging"]
    document_id: DocumentId
    issued_at: datetime | None = None
    conclusion_text: str | None = None
    result_ids: list[ResourceId] = Field(default_factory=list)


class Appointment(PatientResource):
    resource_type: ClassVar[str] = "Appointment"
    category: ClassVar[DataCategory | None] = DataCategory.APPOINTMENTS

    slot_id: SlotId
    practitioner_role_id: PractitionerRoleId
    organization_id: OrganizationId
    start: datetime
    status: AppointmentStatus
    case_id: CaseId | None = None
    sensitive: bool = False  # booked from a private consult (PRV-12)


# ----- Consent and provenance -----------------------------------------------------------------


class Consent(PatientResource):
    resource_type: ClassVar[str] = "Consent"

    grantee_id: UserId
    grantee_role: Literal["guardian", "doctor"]
    categories: frozenset[DataCategory]
    purposes: frozenset[str]
    period: Period
    status: Literal["active", "inactive", "rejected"]
    version: int = 1


class ProvenanceEntity(Contract):
    document_id: DocumentId | None = None
    page: int | None = None
    line: int | None = None
    quote: str | None = None  # short supporting quote; encrypted
    communication_id: ResourceId | None = None  # for patient_reported facts


class Provenance(PatientResource):
    resource_type: ClassVar[str] = "Provenance"
    encrypted_fields: ClassVar[frozenset[str]] = frozenset({"entity"})

    targets: list[Reference]
    recorded_at: datetime
    agent: str  # who: user id, module or agent kind
    activity: Literal["entered", "extracted", "imported", "inferred", "patient_reported"]
    entity: ProvenanceEntity


class Communication(PatientResource):
    """A message delivered to a person in a case (cases module)."""

    resource_type: ClassVar[str] = "Communication"
    category: ClassVar[DataCategory | None] = DataCategory.CONVERSATIONS
    encrypted_fields: ClassVar[frozenset[str]] = frozenset({"payload"})

    case_id: CaseId
    sender: str  # role or agent kind
    recipient: str
    payload: str
    in_response_to: ResourceId | None = None
    simulated: bool = False


CLINICAL_RESOURCES: tuple[type[PatientResource], ...] = (
    Patient,
    RelatedPerson,
    Condition,
    AllergyIntolerance,
    MedicationStatement,
    MedicationAdministration,
    Observation,
    DiagnosticReport,
    Appointment,
    Provenance,
    Communication,
)
