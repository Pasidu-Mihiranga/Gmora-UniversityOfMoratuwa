"""Building the doctor brief from structured data (spec 05 section 4, CON-9, invariant 9).

Pure and deterministic: the same inputs give the same brief. Inputs are what the treating doctor
may see, already filtered by the access policy. The patient's chat text is never an input, so it
cannot appear; facts the patient reported appear as structured facts with the message they came
from as their source. Clashes come from `safety.check_clashes`, never from a model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from suwapath.contracts.briefs import DEFAULT_ORDER, BriefLine, BriefSection, Source
from suwapath.contracts.cases import CaseFact, CaseState
from suwapath.contracts.fhir.base import CodeableConcept
from suwapath.contracts.fhir.resources import Observation
from suwapath.contracts.ids import ResourceId
from suwapath.contracts.ports.care import AdherenceSummary
from suwapath.contracts.ports.record import RecordSummary
from suwapath.contracts.ports.safety import Clash

S = BriefSection
RESULTS_WINDOW = timedelta(days=180)
MAX_RESULTS = 6
RED_FLAG_FACT = "red_flag."
COMPUTED: frozenset[Source] = frozenset({"safety", "case", "care"})  # named by the rule or data


@dataclass(frozen=True)
class RequiredFact:
    """A fact the doctor needs that the record may lack (spec 16 step 5: the home glucose)."""

    fact_key: str
    question_key: str
    why_key: str
    condition_codes: frozenset[str]
    condition_words: tuple[str, ...]
    observation_words: tuple[str, ...]
    fresh_for: timedelta


REQUIRED_FACTS: tuple[RequiredFact, ...] = (
    RequiredFact(
        fact_key="home_glucose_reading",
        question_key="q.home_glucose_reading",
        why_key="why.glucose_for_diabetes",
        condition_codes=frozenset({"44054006", "73211009", "E11", "E10"}),  # SNOMED, ICD-10
        condition_words=("diabetes",),
        observation_words=("glucose",),
        fresh_for=timedelta(days=7),
    ),
)


def build_lines(
    case: CaseState,
    record: RecordSummary | None,
    adherence: AdherenceSummary | None,
    clashes: Sequence[Clash],
    now: datetime,
    questions: Sequence[ResourceId] = (),
) -> list[BriefLine]:
    """`questions`: the patient's "Ask my doctor" messages, shown as links, never as text."""
    lines: list[BriefLine] = []
    lines += _alerts(case, clashes)
    lines += _navigation(case)
    if record is not None:
        lines += [
            _line(
                S.CONDITIONS, _name(c.code), c.clinical_status, "record", (c.id, *c.provenance_ids)
            )
            for c in record.conditions
            if c.clinical_status in ("active", "recurrence")
        ]
        lines += [
            _line(
                S.MEDICATIONS,
                _name(m.medication),
                m.dosage.text,
                "record",
                (m.id, *m.provenance_ids),
            )
            for m in record.medications
            if m.status == "active"
        ]
        lines += [
            _line(
                S.ALLERGIES,
                _name(a.code),
                f"criticality {a.criticality}",
                "record",
                (a.id, *a.provenance_ids),
            )
            for a in record.allergies
        ]
        lines += _results(record.recent_observations, now)
    lines += _reported_results(case)
    if adherence is not None:
        lines.append(_adherence(adherence))
    lines += [
        _line(S.IMAGING, f.key.replace("_", " "), _value(f), "imaging", f.provenance_ids)
        for f in case.facts
        if f.source == "imaging"
    ]
    lines += [
        _line(S.OPEN_QUESTIONS, q.key.replace("_", " "), None, "case", ())
        for q in case.open_questions
        if q.for_role == "doctor"
    ]
    lines += [
        _line(S.OPEN_QUESTIONS, "Patient question", None, "patient_reported", (q,))
        for q in questions
    ]
    return lines


def missing_facts(
    case: CaseState, record: RecordSummary | None, now: datetime
) -> list[RequiredFact]:
    """Required facts the record and the case do not answer yet."""
    if record is None:
        return []
    known = {f.key for f in case.facts}
    missing = []
    for rule in REQUIRED_FACTS:
        if rule.fact_key in known or not any(
            _has_condition(c.code, rule) for c in record.conditions
        ):
            continue
        fresh = [
            o
            for o in record.recent_observations
            if any(w in _name(o.code).casefold() for w in rule.observation_words)
            and now - o.effective_at <= rule.fresh_for
        ]
        if not fresh:
            missing.append(rule)
    return missing


def ordered(
    lines: list[BriefLine], order: Sequence[BriefSection] = DEFAULT_ORDER
) -> list[BriefLine]:
    rank = {s: n for n, s in enumerate((*order, *(s for s in BriefSection if s not in order)))}
    return sorted(lines, key=lambda line: rank[line.section])


# ----- sections ---------------------------------------------------------------------------------


def _alerts(case: CaseState, clashes: Sequence[Clash]) -> list[BriefLine]:
    lines = [
        _line(S.ALERTS, f"Red flag {hit.rule_id}", hit.urgency.value, "safety", ())
        for hit in case.red_flags
    ]
    for f in case.facts:
        if f.key.startswith(RED_FLAG_FACT):
            rule = f.key.removeprefix(RED_FLAG_FACT)
            lines.append(
                _line(S.ALERTS, f"Red flag {rule}", _value(f), "patient_reported", f.provenance_ids)
            )
    for clash in clashes:
        ids = tuple(ResourceId(u) for u in (_uuid(x) for x in clash.between) if u is not None)
        lines.append(
            BriefLine(
                section=S.ALERTS,
                label=f"Clash: {clash.kind.replace('_', ' ')}",
                value=f"{clash.severity}; {clash.reference}"[:200],
                source="safety",
                provenance_ids=ids,
                verified=bool(ids),
            )
        )
    return lines


def _navigation(case: CaseState) -> list[BriefLine]:
    lines = []
    if case.urgency is not None:
        floor = f" (rules floor {case.urgency_floor.value})" if case.urgency_floor else ""
        lines.append(_line(S.NAVIGATION, "Urgency", f"{case.urgency.value}{floor}", "case", ()))
    route = case.suggested_route
    if route is not None:
        others = ", ".join(s.value for s in route.alternatives)
        value = route.primary_specialty.value + (f"; also considered {others}" if others else "")
        lines.append(_line(S.NAVIGATION, "Specialty", value, "case", ()))
    for f in case.facts:
        if f.source != "patient_reported" or f.key.startswith(RED_FLAG_FACT) or _is_required(f.key):
            continue
        label = f.key.replace("_", " ") + ("" if f.present else " (denied)")
        lines.append(_line(S.NAVIGATION, label, _value(f), "patient_reported", f.provenance_ids))
    return lines


def _results(observations: Sequence[Observation], now: datetime) -> list[BriefLine]:
    recent = sorted(
        (o for o in observations if now - o.effective_at <= RESULTS_WINDOW),
        key=lambda o: o.effective_at,
        reverse=True,
    )[:MAX_RESULTS]
    lines = []
    for o in recent:
        value = (
            f"{o.value.value:g} {o.value.unit or ''}".strip()
            if not isinstance(o.value, str)
            else o.value
        )
        flag = f", {o.interpretation}" if o.interpretation and o.interpretation != "normal" else ""
        source: Source = "report" if o.derived_from is not None else "record"
        lines.append(
            _line(
                S.RESULTS,
                _name(o.code),
                f"{value}{flag}, {o.effective_at:%d %b %Y}",
                source,
                (o.id, *o.provenance_ids),
            )
        )
    return lines


def _reported_results(case: CaseState) -> list[BriefLine]:
    """Facts the doctor asked for, as the patient reported them (spec 16 step 6)."""
    return [
        _line(S.RESULTS, f.key.replace("_", " "), _value(f), "patient_reported", f.provenance_ids)
        for f in case.facts
        if _is_required(f.key)
    ]


def _adherence(summary: AdherenceSummary) -> BriefLine:
    days = summary.days[-7:]
    taken, missed = sum(d.taken for d in days), sum(d.missed for d in days)
    slots = ", ".join(f"{s.value} {n}" for s, n in sorted(summary.missed_by_slot.items()) if n)
    value = f"taken {taken}, missed {missed} in {len(days)} days" + (
        f"; missed: {slots}" if slots else ""
    )
    return _line(S.ADHERENCE, "Medicines taken", value, "care", ())


# ----- helpers ------------------------------------------------------------------------------------


def _line(
    section: BriefSection,
    label: str,
    value: str | None,
    source: Source,
    provenance: Sequence[ResourceId],
) -> BriefLine:
    ids = tuple(dict.fromkeys(provenance))
    return BriefLine(
        section=section,
        label=label[:200],
        value=value[:200] if value else None,
        source=source,
        provenance_ids=ids,
        verified=bool(ids) or source in COMPUTED,
    )


def _name(concept: CodeableConcept) -> str:
    if concept.text:
        return concept.text
    for coding in concept.coding:
        if coding.display:
            return coding.display
    return concept.coding[0].code if concept.coding else "unnamed"


def _value(fact: CaseFact) -> str | None:
    return None if fact.value is None else str(fact.value)


def _is_required(key: str) -> bool:
    return any(r.fact_key == key for r in REQUIRED_FACTS)


def _has_condition(code: CodeableConcept, rule: RequiredFact) -> bool:
    if any(c.code in rule.condition_codes for c in code.coding):
        return True
    return any(w in _name(code).casefold() for w in rule.condition_words)


def _uuid(text: str) -> UUID | None:
    try:
        return UUID(text)
    except ValueError:
        return None
