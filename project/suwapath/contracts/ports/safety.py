"""safety: lexicon, red flags, urgency floor, wording rules, clash list (SAF-1 to SAF-32)."""

from __future__ import annotations

from datetime import date
from typing import Literal, Protocol

from suwapath.contracts.common import Contract, UntrustedText
from suwapath.contracts.enums import Language, Urgency
from suwapath.contracts.fhir.resources import AllergyIntolerance, Condition, MedicationStatement


class PatientContext(Contract):
    """Minimal non-identifying context the rules need."""

    age_years: int | None = None
    sex: Literal["female", "male", "other", "unknown"] = "unknown"
    pregnant: bool = False
    postpartum: bool = False
    pregnancy_week: int | None = None
    chronic_codes: frozenset[str] = frozenset()  # e.g. {"diabetes", "hypertension"}
    missed_doses_48h: int = 0
    on_date: date | None = None


class RedFlagMatch(Contract):
    rule_id: str
    label_key: str
    urgency: Urgency
    matched_concepts: tuple[str, ...]


class SafetyAssessment(Contract):
    concepts: frozenset[str]
    red_flags: tuple[RedFlagMatch, ...]
    urgency_floor: Urgency | None
    emergency: bool
    help_lines: tuple[str, ...] = ()  # e.g. ("1990",), ("1990", "1926")
    rules_version: str


class WordingHit(Contract):
    pattern_id: str
    category: Literal["diagnosis", "reassurance", "dosing", "probability", "banned_term"]
    language: Language


class Clash(Contract):
    kind: Literal["drug_drug", "drug_allergy", "duplicate_therapy", "drug_condition"]
    severity: Literal["contraindicated", "major", "moderate"]
    between: tuple[str, ...]  # medication or allergy resource ids as strings
    reference: str  # public source for the interaction (SAF-31)


class SafetyService(Protocol):
    def assess(
        self, text: UntrustedText, context: PatientContext, language: Language | None = None
    ) -> SafetyAssessment:
        """Deterministic, no model, no I/O; must finish in under 50 ms (13 targets)."""
        ...

    def check_wording(self, text: str, language: Language) -> list[WordingHit]: ...

    def check_clashes(
        self,
        medications: list[MedicationStatement],
        allergies: list[AllergyIntolerance],
        conditions: list[Condition],
    ) -> list[Clash]: ...
