"""navigation: knowledge-driven engine, doctor matching, trips (NAV-1 to NAV-16)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Protocol

from pydantic import Field

from suwapath.contracts.common import Contract, Page, UntrustedText
from suwapath.contracts.enums import Language, Specialty, Urgency
from suwapath.contracts.fhir.base import GeoPoint
from suwapath.contracts.ids import (
    CaseId,
    OrganizationId,
    PractitionerId,
    PractitionerRoleId,
    ResourceId,
    SlotId,
)
from suwapath.contracts.ports.safety import PatientContext
from suwapath.contracts.principal import Principal


class Finding(Contract):
    concept: str
    present: bool
    onset: str | None = None
    duration: str | None = None
    severity: Literal["mild", "moderate", "severe"] | None = None
    site: str | None = None
    provenance_id: ResourceId | None = None


class SpecialtyOption(Contract):
    specialty: Specialty
    probability: float = Field(ge=0, le=1)
    reason_key: str


class NavigationResult(Contract):
    """NAV-6. Never contains condition hypotheses (NAV-2)."""

    urgency: Urgency
    urgency_floor: Urgency | None
    primary_specialty: Specialty
    alternatives: tuple[SpecialtyOption, ...] = ()
    confidence: float = Field(ge=0, le=1)
    low_confidence: bool
    findings: tuple[Finding, ...] = ()
    negatives: tuple[Finding, ...] = ()
    questions_asked: int = 0
    kb_version: str
    rules_version: str


class NavigationQuestion(Contract):
    concept: str
    question_key: str
    text: str | None = None  # the reviewed question in the case language (kb/questions.yaml)
    answer_type: Literal["yes_no", "choice", "number", "duration"]
    choices: tuple[str, ...] = ()
    red_flag_screen: bool = False  # does not count toward the 6-question cap (NAV-1)


class NavigationStep(Contract):
    """Either the next question, a final result, or an emergency stop.

    `findings` are what this step newly read from the patient's words. The caller records them as
    case facts (`FactRecorded`), so the case events stay the only navigation state (NAV-4)."""

    question: NavigationQuestion | None = None
    result: NavigationResult | None = None
    emergency: bool = False
    findings: tuple[Finding, ...] = ()


class DoctorQuery(Contract):
    specialty: Specialty
    near: GeoPoint
    date_from: date
    date_to: date
    language: Language | None = None
    time_of_day: Literal["morning", "afternoon", "evening", "any"] = "any"
    max_km: float = 25
    name: str | None = None  # direct channelling (NAV-13), trigram match
    organization_id: OrganizationId | None = None
    prefer_practitioner_ids: tuple[PractitionerId, ...] = ()  # continuity (NAV-15)


class DoctorOption(Contract):
    practitioner_id: PractitionerId
    practitioner_role_id: PractitionerRoleId
    display_name: str  # directory data, not PHI
    specialty: Specialty
    organization_id: OrganizationId
    organization_name: str
    distance_km: float
    next_slot_id: SlotId | None
    next_slot_at: datetime | None
    fee_lkr: int
    languages: tuple[Language, ...] = ()
    reason_key: str  # why this card is shown (NAV-7)
    continuity: bool = False


class TripStop(Contract):
    slot_id: SlotId
    practitioner_role_id: PractitionerRoleId
    start: datetime


class Itinerary(Contract):
    day: date
    stops: tuple[TripStop, ...]
    total_wait_minutes: int
    total_travel_minutes: int


class NavigationService(Protocol):
    async def start(
        self,
        principal: Principal,
        case_id: CaseId,
        text: UntrustedText,
        context: PatientContext,
        language: Language,
    ) -> NavigationStep: ...

    async def answer(
        self,
        principal: Principal,
        case_id: CaseId,
        concept: str,
        answer: str | float | bool,
        context: PatientContext | None = None,
    ) -> NavigationStep: ...

    async def result(self, principal: Principal, case_id: CaseId) -> NavigationResult | None: ...

    async def find_doctors(
        self, query: DoctorQuery, cursor: str | None, limit: int
    ) -> Page[DoctorOption]:
        """Ranking never uses commissions or fees as a boost (NAV-3, NAV-10)."""
        ...

    async def plan_trip(
        self, specialties: list[Specialty], near: GeoPoint, date_from: date, date_to: date
    ) -> list[Itinerary]: ...
