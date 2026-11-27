"""The synthetic world is deterministic and matches spec 16 (SEED-1, SEED-2)."""

from collections import Counter
from datetime import timedelta

from suwapath.contracts.enums import DataCategory, DoseSlot, Role
from suwapath.seed.generator import ANCHOR, expand_doses, generate


def test_same_seed_same_bytes_and_different_seed_differs() -> None:
    assert generate(42).model_dump_json() == generate(42).model_dump_json()
    assert generate(42).model_dump_json() != generate(7).model_dump_json()


def test_counts_match_spec_16() -> None:
    world = generate()
    assert len(world.organizations) == 6
    assert len(world.practitioners) == 24
    assert len(world.patients) == 30
    ages = [ANCHOR.year - p.birth_date.year for p in world.patients]
    assert sum(a >= 60 for a in ages) == 20
    assert sum(a < 18 for a in ages) == 3
    assert len(world.schedules) > 100  # 14 days of sessions


def test_amma_scenario_is_exact() -> None:
    world = generate()
    amma = world.patients[0]
    assert (
        amma.name.text == "Kamala Perera"
        and amma.district == "Kurunegala"
        and amma.language.value == "si"
    )
    meds = {
        m.medication.coding[0].code for m in world.medications if m.patient_id == amma.patient_id
    }
    assert meds == {"metformin", "amlodipine", "atorvastatin", "paracetamol"}
    assert [a.code.coding[0].code for a in world.allergies if a.patient_id == amma.patient_id] == [
        "penicillin"
    ]
    nimal = next(r for r in world.related_persons if r.patient_id == amma.patient_id)
    assert nimal.name.text == "Nimal Perera" and nimal.relationship == "son" and nimal.lives_abroad
    consent = next(c for c in world.consents if c.patient_id == amma.patient_id)
    assert (
        DataCategory.ADHERENCE in consent.categories
        and DataCategory.CONVERSATIONS not in consent.categories
    )
    logins = {u.login for u in world.users}
    assert {
        "amma@demo.suwapath.lk",
        "nimal@demo.suwapath.lk",
        "dr.silva@demo.suwapath.lk",
        "admin.lotus@demo.suwapath.lk",
        "ops@demo.suwapath.lk",
    } <= logins


def test_amma_missed_the_last_two_evenings() -> None:
    world = generate()
    plan = next(a for a in world.adherence if a.patient_id == world.patients[0].patient_id)
    history = expand_doses(world, plan)
    last_two = {(ANCHOR - timedelta(days=d)) for d in (1, 2)}
    evening = [
        taken
        for _, when, slot, taken in history
        if slot is DoseSlot.EVENING and (when + timedelta(hours=5, minutes=30)).date() in last_two
    ]
    assert evening and not any(evening)
    assert expand_doses(world, plan) == history  # deterministic


def test_every_fact_has_provenance_and_brief_clashes_are_labelled() -> None:
    world = generate()
    provenance_ids = {p.id for p in world.provenance}
    for resource in (*world.conditions, *world.allergies, *world.medications, *world.observations):
        assert resource.provenance_ids and set(resource.provenance_ids) <= provenance_ids  # CON-9
    planted = Counter(c for label in world.brief_labels for c in label.planted_clashes)
    assert set(planted) == {"allergy", "interaction", "duplicate", "nsaid_ckd"}


def test_users_are_unique_and_roles_complete() -> None:
    world = generate()
    assert len({u.login for u in world.users}) == len(world.users)
    roles = Counter(u.role for u in world.users)
    assert roles[Role.PATIENT] == 30 and roles[Role.DOCTOR] == 24
    assert (
        roles[Role.HOSPITAL_ADMIN] == 1
        and roles[Role.SYSTEM_ADMIN] == 1
        and roles[Role.GUARDIAN] >= 10
    )
