"""FHIR R4-shaped building blocks (CON-7).

These follow FHIR names and references for the subset SuwaPath uses. They are not a full FHIR
implementation; `record` exports them as a FHIR R4 Bundle.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar, Literal

from pydantic import Field

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import DataCategory
from suwapath.contracts.ids import PatientId, ResourceId


class Coding(Contract):
    system: str | None = None  # e.g. "http://snomed.info/sct", "http://loinc.org", "local"
    code: str
    display: str | None = None


class CodeableConcept(Contract):
    coding: list[Coding] = Field(default_factory=list)
    text: str | None = None


class Reference(Contract):
    """`resource_type/id` reference, e.g. Reference(type="Practitioner", id=...)."""

    type: str
    id: ResourceId


class Period(Contract):
    start: datetime | None = None
    end: datetime | None = None


class Quantity(Contract):
    value: float
    unit: str | None = None


class Meta(Contract):
    version_id: int = 1
    last_updated: datetime
    source: str  # owning module


class HumanName(Contract):
    given: list[str] = Field(default_factory=list)
    family: str | None = None
    text: str | None = None  # as written, including Sinhala script


class ContactPoint(Contract):
    system: Literal["phone", "email", "sms"]
    value: str


class Address(Contract):
    text: str | None = None
    city: str | None = None
    district: str | None = None


class GeoPoint(Contract):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Resource(Contract):
    """Common fields of every resource.

    Subclasses declare:
    - `category`: the DataCategory that consent decisions use (None for directory resources);
    - `encrypted_fields`: top-level fields stored encrypted with the patient's key (SEC-23).
    """

    resource_type: ClassVar[str]
    category: ClassVar[DataCategory | None] = None
    encrypted_fields: ClassVar[frozenset[str]] = frozenset()

    id: ResourceId
    meta: Meta
    provenance_ids: list[ResourceId] = Field(default_factory=list)


class PatientResource(Resource):
    """A resource that belongs to one patient (partition key, DB-3)."""

    patient_id: PatientId


AgeBand = Literal["0-4", "5-17", "18-39", "40-59", "60-74", "75+"]


def age_band(birth_date: date, on: date) -> AgeBand:
    years = on.year - birth_date.year - ((on.month, on.day) < (birth_date.month, birth_date.day))
    if years < 5:
        return "0-4"
    if years < 18:
        return "5-17"
    if years < 40:
        return "18-39"
    if years < 60:
        return "40-59"
    if years < 75:
        return "60-74"
    return "75+"
