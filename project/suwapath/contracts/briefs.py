"""The doctor brief (spec 05 section 4, CON-9, invariant 9).

A brief is built from structured data only: the record, the case (facts, red flags, urgency,
route, open questions), adherence and the safety clash check. It never contains the patient's
chat text and never a condition hypothesis (NAV-8). Every line names its sources; a line without
one is marked unverified.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from suwapath.contracts.common import Contract
from suwapath.contracts.ids import CaseId, PatientId, ResourceId


class BriefSection(StrEnum):
    """Default order (spec 05 section 4); a doctor's preference may reorder them."""

    ALERTS = "alerts"  # red flags and medicine clashes
    REASON = "reason"
    NAVIGATION = "navigation"  # findings, negatives, urgency, specialties considered
    CONDITIONS = "conditions"
    MEDICATIONS = "medications"
    ALLERGIES = "allergies"
    RESULTS = "results"
    ADHERENCE = "adherence"
    IMAGING = "imaging"
    OPEN_QUESTIONS = "open_questions"


DEFAULT_ORDER: tuple[BriefSection, ...] = tuple(BriefSection)

Source = Literal["record", "report", "imaging", "patient_reported", "case", "safety", "care"]


class BriefLine(Contract):
    section: BriefSection
    label: str = Field(max_length=200)  # what it is, e.g. a condition or medicine name
    value: str | None = Field(default=None, max_length=200)  # e.g. "210 mg/dL, 28 Nov"
    source: Source
    provenance_ids: tuple[ResourceId, ...] = ()  # record resources, case events, communications
    verified: bool  # False when no source could be named (invariant 9)


class Brief(Contract):
    id: ResourceId
    case_id: CaseId
    patient_id: PatientId
    version: int = Field(ge=1)
    built_at: datetime
    order: tuple[BriefSection, ...] = DEFAULT_ORDER
    lines: tuple[BriefLine, ...] = ()
    missing_facts: tuple[str, ...] = ()  # fact keys asked from the patient, not yet answered
    hidden_categories_count: int = 0  # record parts the doctor may not see (sensitive)
