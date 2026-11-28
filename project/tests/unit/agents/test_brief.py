"""Invariant 9 and CON-9 on the brief builder: every line has a source, no chat text, no hypotheses,
clashes from safety only, the doctor's order, and the missing home glucose of spec 16 step 5."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from suwapath.contracts.briefs import BriefSection as S
from suwapath.contracts.cases import CaseFact, CaseState, RedFlagHit, RouteSuggestion
from suwapath.contracts.enums import CaseStatus, DoseSlot, Language, Specialty, Urgency
from suwapath.contracts.fhir.base import CodeableConcept, Coding, Meta, Quantity, Reference
from suwapath.contracts.fhir.resources import (
    AllergyIntolerance,
    Condition,
    Dosage,
    DoseTiming,
    MedicationStatement,
    Observation,
)
from suwapath.contracts.ids import CaseId, PatientId, ResourceId
from suwapath.contracts.ports.care import AdherenceDay, AdherenceSummary
from suwapath.contracts.ports.record import RecordSummary
from suwapath.contracts.ports.safety import Clash
from suwapath.modules.agents.domain.brief import build_lines, missing_facts, ordered

NOW = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
PATIENT = PatientId(uuid4())
META = Meta(last_updated=NOW, source="record")
CHAT = "I feel dizzy since yesterday and my son is worried"


def rid() -> ResourceId:
    return ResourceId(uuid4())


def concept(text: str, code: str = "x") -> CodeableConcept:
    return CodeableConcept(text=text, coding=[Coding(code=code)])


DIABETES = Condition(
    id=rid(),
    meta=META,
    patient_id=PATIENT,
    code=concept("Type 2 diabetes", "44054006"),
    clinical_status="active",
)
OLD_CONDITION = Condition(
    id=rid(),
    meta=META,
    patient_id=PATIENT,
    code=concept("Old fracture"),
    clinical_status="resolved",
)
METFORMIN = MedicationStatement(
    id=rid(),
    meta=META,
    patient_id=PATIENT,
    medication=concept("Metformin"),
    dosage=Dosage(
        text="500 mg twice daily", timing=DoseTiming(slots=[DoseSlot.MORNING, DoseSlot.EVENING])
    ),
    status="active",
)
PENICILLIN = AllergyIntolerance(
    id=rid(), meta=META, patient_id=PATIENT, code=concept("Penicillin"), criticality="high"
)
REPORT = rid()
HBA1C = Observation(
    id=rid(),
    meta=META,
    patient_id=PATIENT,
    code=concept("HbA1c"),
    value=Quantity(value=8.1, unit="%"),
    interpretation="high",
    effective_at=NOW - timedelta(days=20),
    derived_from=Reference(type="DiagnosticReport", id=REPORT),
)
RECORD = RecordSummary(
    patient=None,
    conditions=(DIABETES, OLD_CONDITION),
    medications=(METFORMIN,),
    allergies=(PENICILLIN,),
    recent_observations=(HBA1C,),
)
MESSAGE = rid()


def case(**changes: object) -> CaseState:
    base = CaseState(
        id=CaseId(uuid4()),
        patient_id=PATIENT,
        status=CaseStatus.ASSESSED,
        language=Language.SI,
        urgency=Urgency.URGENT_24H,
        urgency_floor=Urgency.URGENT_24H,
        facts=(
            CaseFact(key="dizziness_since", value="2 days", provenance_ids=(MESSAGE,)),
            CaseFact(key="chest_pain", present=False, value="no", provenance_ids=(MESSAGE,)),
            CaseFact(key="red_flag.RF-GER-003", value="urgent_24h", provenance_ids=(MESSAGE,)),
        ),
        suggested_route=RouteSuggestion(
            urgency=Urgency.URGENT_24H,
            primary_specialty=Specialty.GENERAL_MEDICINE,
            alternatives=(Specialty.CARDIOLOGY,),
            confidence=0.7,
            low_confidence=False,
            kb_version="kb-1",
        ),
        version=7,
    )
    return base.model_copy(update=changes)


ADHERENCE = AdherenceSummary(
    patient_id=PATIENT,
    days=(
        AdherenceDay(
            day=NOW.date(), taken=1, missed=1, pending=0, missed_slots=(DoseSlot.EVENING,)
        ),
    ),
    missed_by_slot={DoseSlot.EVENING: 1},
    current_streak_days=0,
)


def lines(**kw: object) -> list:
    return build_lines(case(**kw), RECORD, ADHERENCE, [], NOW)


def test_every_line_has_a_source_or_is_marked_unverified() -> None:
    for line in lines():
        assert line.verified == (
            bool(line.provenance_ids) or line.source in ("safety", "case", "care")
        )
    unsourced = case(facts=(CaseFact(key="dizziness_since", value="2 days"),))
    line = next(
        x for x in build_lines(unsourced, None, None, [], NOW) if x.label == "dizziness since"
    )
    assert not line.verified


def test_record_lines_point_at_their_resources() -> None:
    by_label = {x.label: x for x in lines()}
    assert by_label["Type 2 diabetes"].provenance_ids[0] == DIABETES.id
    assert by_label["Penicillin"].value == "criticality high"
    assert by_label["Metformin"].value == "500 mg twice daily"
    hba1c = by_label["HbA1c"]
    assert hba1c.source == "report" and hba1c.value == "8.1 %, high, 08 Nov 2026"
    assert "Old fracture" not in by_label  # resolved conditions are left out


def test_no_chat_text_and_no_hypotheses() -> None:
    text = " ".join(f"{x.label} {x.value or ''}" for x in lines())
    assert CHAT not in text and "worried" not in text
    assert "chest pain (denied)" in text  # negatives are shown as negatives (NAV-8)
    assert "probable" not in text and "likely" not in text


def test_clashes_come_from_safety_with_their_resources() -> None:
    clash = Clash(
        kind="drug_allergy",
        severity="contraindicated",
        between=(str(METFORMIN.id), str(PENICILLIN.id)),
        reference="BNF",
    )
    alert = next(
        x for x in build_lines(case(), RECORD, None, [clash], NOW) if x.label.startswith("Clash")
    )
    assert alert.section is S.ALERTS and alert.source == "safety"
    assert set(alert.provenance_ids) == {METFORMIN.id, PENICILLIN.id}


def test_red_flags_and_urgency_lead_the_brief() -> None:
    flagged = case(
        red_flags=(RedFlagHit(rule_id="RF-CARD-001", urgency=Urgency.EMERGENCY, rules_version="1"),)
    )
    result = ordered(build_lines(flagged, RECORD, ADHERENCE, [], NOW))
    assert result[0].section is S.ALERTS
    assert [x.section for x in result] == sorted((x.section for x in result), key=list(S).index)


def test_a_doctors_order_is_respected() -> None:
    order = (S.MEDICATIONS, S.ALLERGIES)
    result = ordered(lines(), order)
    assert [x.section for x in result][:2] == [S.MEDICATIONS, S.ALLERGIES]


def test_home_glucose_is_missing_for_a_diabetic_without_a_recent_reading() -> None:
    assert [r.fact_key for r in missing_facts(case(), RECORD, NOW)] == ["home_glucose_reading"]
    answered = case(
        facts=(CaseFact(key="home_glucose_reading", value=210.0, provenance_ids=(MESSAGE,)),)
    )
    assert missing_facts(answered, RECORD, NOW) == []
    line = next(
        x for x in build_lines(answered, RECORD, None, [], NOW) if x.label == "home glucose reading"
    )
    assert (line.section, line.source, line.value, line.verified) == (
        S.RESULTS,
        "patient_reported",
        "210.0",
        True,
    )


def test_no_missing_fact_without_the_condition() -> None:
    no_diabetes = RECORD.model_copy(update={"conditions": (OLD_CONDITION,)})
    assert missing_facts(case(), no_diabetes, NOW) == []
