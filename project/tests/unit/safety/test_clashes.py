"""The clash list (SAF-30 to SAF-32) on the demo formulary: the clashes the seed world plants are
found, allergies match by substance and by class, only active entries count, and the output is
deterministic with a reference for every clash."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from suwapath.contracts.fhir.base import CodeableConcept, Coding, Meta
from suwapath.contracts.fhir.resources import (
    AllergyIntolerance,
    Condition,
    Dosage,
    DoseTiming,
    MedicationStatement,
)
from suwapath.contracts.ids import PatientId, ResourceId
from suwapath.contracts.ports.safety import Clash
from suwapath.modules.safety.application.service import CONTENT as SAFETY_CONTENT
from suwapath.modules.safety.application.service import RuleSafetyService
from suwapath.seed import catalog

SAFETY = RuleSafetyService.from_content()
PATIENT = PatientId(uuid4())
META = Meta(last_updated=datetime(2026, 11, 28, tzinfo=UTC), source="test")


def med(
    name: str,
    status: Literal["active", "on-hold", "stopped", "completed"] = "active",
    klass: str | None = None,
) -> MedicationStatement:
    coding = [Coding(system="local", code=name)]
    if klass:
        coding.append(Coding(system="class", code=klass))
    return MedicationStatement(
        id=ResourceId(uuid4()),
        meta=META,
        patient_id=PATIENT,
        medication=CodeableConcept(coding=coding, text=name.replace("_", " ").capitalize()),
        dosage=Dosage(timing=DoseTiming()),
        status=status,
    )


def allergy(code: str, klass: str | None = None) -> AllergyIntolerance:
    coding = [Coding(system="local", code=code)]
    if klass:
        coding.append(Coding(system="class", code=klass))
    return AllergyIntolerance(
        id=ResourceId(uuid4()), meta=META, patient_id=PATIENT, code=CodeableConcept(coding=coding)
    )


def condition(
    code: str, status: Literal["active", "recurrence", "remission", "resolved"] = "active"
) -> Condition:
    return Condition(
        id=ResourceId(uuid4()),
        meta=META,
        patient_id=PATIENT,
        code=CodeableConcept(coding=[Coding(system="local", code=code)]),
        clinical_status=status,
    )


def check(
    meds: list[MedicationStatement],
    allergies: list[AllergyIntolerance] | None = None,
    conditions: list[Condition] | None = None,
) -> list[Clash]:
    return SAFETY.check_clashes(meds, allergies or [], conditions or [])


def ids(*resources: MedicationStatement | AllergyIntolerance | Condition) -> tuple[str, ...]:
    return tuple(sorted(str(r.id) for r in resources))


# ----- the planted seed clashes (SEED-2) --------------------------------------------------------


def test_a_penicillin_allergy_with_amoxicillin_is_contraindicated() -> None:
    pen, amox = allergy("penicillin", "penicillin"), med("amoxicillin", klass="penicillin")
    [clash] = check([amox, med("metformin")], [pen])
    assert (clash.kind, clash.severity, clash.between) == (
        "drug_allergy",
        "contraindicated",
        ids(pen, amox),
    )


def test_warfarin_with_aspirin_is_one_major_interaction() -> None:
    warfarin, aspirin = med("warfarin"), med("aspirin")
    [clash] = check([warfarin, aspirin])
    assert (clash.kind, clash.severity, clash.between) == (
        "drug_drug",
        "major",
        ids(warfarin, aspirin),
    )


def test_two_statins_are_a_duplicate_therapy() -> None:
    one, two = med("atorvastatin"), med("rosuvastatin")
    [clash] = check([one, two])
    assert (clash.kind, clash.severity, clash.between) == (
        "duplicate_therapy",
        "moderate",
        ids(one, two),
    )


def test_an_nsaid_in_kidney_disease_is_a_major_caution() -> None:
    diclofenac, ckd = med("diclofenac"), condition("ckd_stage3")
    [clash] = check([diclofenac], conditions=[ckd])
    assert (clash.kind, clash.severity, clash.between) == (
        "drug_condition",
        "major",
        ids(diclofenac, ckd),
    )


def test_ammas_record_has_no_clash() -> None:
    meds = [med(m) for m in ("metformin", "amlodipine", "atorvastatin", "paracetamol")]
    conditions = [condition(c) for c in ("diabetes_type2", "hypertension", "osteoarthritis")]
    assert check(meds, [allergy("penicillin", "penicillin")], conditions) == []


# ----- allergies by substance and class (SAF-32) ------------------------------------------------


def test_a_penicillin_allergy_matches_co_amoxiclav_and_warns_on_cephalosporins() -> None:
    pen = allergy("penicillin")
    clashes = check([med("co_amoxiclav"), med("cephalexin")], [pen])
    assert [c.severity for c in clashes] == ["contraindicated", "moderate"]  # worst first


def test_an_nsaid_allergy_matches_aspirin_and_ibuprofen() -> None:
    clashes = check([med("aspirin"), med("ibuprofen")], [allergy("nsaid", "nsaid")])
    assert [c.kind for c in clashes if c.severity == "contraindicated"] == ["drug_allergy"] * 2


def test_an_allergy_to_the_medicine_itself() -> None:
    [clash] = check([med("ciprofloxacin")], [allergy("ciprofloxacin")])
    assert (clash.kind, clash.severity) == ("drug_allergy", "contraindicated")


def test_a_brand_name_is_known_by_its_alias() -> None:
    assert check([med("augmentin")], [allergy("penicillin")])[0].severity == "contraindicated"


def test_an_unknown_medicine_is_checked_by_its_class() -> None:
    assert check([med("new_penicillin_x", klass="penicillin")], [allergy("penicillin")])


# ----- what counts ------------------------------------------------------------------------------


def test_stopped_medicines_and_resolved_conditions_do_not_count() -> None:
    assert check([med("warfarin"), med("aspirin", status="stopped")]) == []
    assert check([med("diclofenac")], conditions=[condition("ckd_stage3", "resolved")]) == []


def test_the_same_medicine_twice_is_not_a_duplicate_therapy() -> None:
    assert check([med("atorvastatin"), med("atorvastatin")]) == []


def test_aspirin_with_clopidogrel_is_not_flagged() -> None:
    assert check([med("aspirin"), med("clopidogrel")]) == []  # dual antiplatelet therapy


def test_the_worst_clash_comes_first_and_the_order_is_stable() -> None:
    meds = [med("simvastatin"), med("clarithromycin"), med("clopidogrel"), med("omeprazole")]
    first, again = check(meds), check(list(reversed(meds)))
    assert [c.severity for c in first] == ["contraindicated", "moderate"]
    assert first == again


# ----- the formulary file (SAF-30, SAF-31) ------------------------------------------------------


def test_the_formulary_is_marked_demo_and_every_rule_has_a_reference() -> None:
    formulary = SAFETY.formulary
    assert len(formulary.medicines) >= 38
    for name in ("metformin", "glibenclamide", "insulin", "warfarin", "co_amoxiclav", "cephalexin"):
        assert name in formulary.medicines
    for rule in (*formulary.interactions, *formulary.drug_conditions):
        assert rule["reference"].strip()
    assert all(entry["reference"].strip() for entry in formulary.allergy_classes.values())
    text = (SAFETY_CONTENT / "formulary.yaml").read_text(encoding="utf-8")
    assert "NOT FOR CLINICAL USE" in text


def test_every_seed_medicine_and_allergy_is_in_the_formulary() -> None:
    formulary = SAFETY.formulary
    for name in catalog.FORMULARY:
        assert formulary.aliases.get(name, name) in formulary.medicines, name
    for code, klass in catalog.ALLERGIES:
        if code != "iodine_contrast":  # a contrast dye, not a medicine on this list
            assert code in formulary.allergy_classes or klass in formulary.allergy_classes, code


def test_clash_ids_are_resource_ids() -> None:
    [clash] = check([med("warfarin"), med("ibuprofen")])
    assert all(UUID(value) for value in clash.between)
