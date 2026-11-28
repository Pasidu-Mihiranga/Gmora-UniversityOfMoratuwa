"""The navigation engine on the real knowledge base: one test per safeguard (spec 08, NAV-1 to
NAV-5). Patients are simulated by a rule "answer yes to these concepts, no to the rest"."""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from suwapath.contracts.enums import Specialty, Urgency
from suwapath.modules.navigation.domain.engine import (
    MAX_QUESTIONS,
    Knowns,
    decide,
    distributions,
    next_question,
    posterior,
)
from suwapath.modules.navigation.domain.kb import KnowledgeBase

KB = KnowledgeBase.load()


def interview(
    present: set[str],
    yes: set[str] = frozenset(),
    *,
    age: int | None = 40,
    sex: str = "female",
    floor: Urgency | None = None,
    unsure: set[str] = frozenset(),
) -> tuple[Knowns, list[tuple[str, bool]]]:
    """Run the engine to the end. Returns the final knowns and (concept, counted) per question."""
    knowns = Knowns(present=frozenset(present), age_years=age, sex=sex, floor=floor)
    asked: list[tuple[str, bool]] = []
    for _ in range(40):
        ask = next_question(KB, knowns)
        if ask is None:
            return knowns, asked
        counted = not ask.red_flag_screen and ask.clarify is None
        asked.append((ask.concept, counted))
        if ask.clarify:
            knowns = replace(knowns, clarified=knowns.clarified | {ask.clarify})
            continue
        answer = ask.concept in yes
        knowns = replace(
            knowns,
            present=knowns.present
            | ({ask.concept} if answer and ask.concept not in unsure else set()),
            absent=knowns.absent
            | ({ask.concept} if not answer and ask.concept not in unsure else set()),
            asked=knowns.asked | {ask.concept},
            counted=knowns.counted + counted,
        )
    raise AssertionError("the engine never stopped")


# ----- the knowledge base (NAV-4) -----------------------------------------------------------------


def test_the_knowledge_base_loads_checked_and_versioned() -> None:
    assert len(KB.groups) == 49
    assert KB.version.startswith("concepts-v1+condition-groups-v1+routing-v1-draft")
    assert all(0 < p < 1 for g in KB.groups for p in g.likelihood.values())
    assert all(g.specialties for g in KB.groups)


def test_every_question_exists_in_both_languages() -> None:
    for question in KB.questions.values():
        assert question.text["en"].strip() and question.text["si"].strip()


# ----- safeguard 1: red flags first, never capped (NAV-1) -----------------------------------------


def test_red_flag_follow_ups_come_first_and_do_not_count() -> None:
    _, asked = interview({"chest_pain"}, age=30)
    first = [concept for concept, _ in asked[:3]]
    assert first == ["pain_spreading", "sweating", "breathless"]
    assert [counted for _, counted in asked[:3]] == [False, False, False]


def test_the_question_cap_never_stops_red_flag_screening() -> None:
    _, asked = interview({"chest_pain", "fever", "headache"}, age=30)
    counted = [c for c, is_counted in asked if is_counted]
    screens = [c for c, is_counted in asked if not is_counted]
    assert len(counted) <= MAX_QUESTIONS
    assert {"pain_spreading", "sweating", "breathless", "stiff_neck"} <= set(screens)


@pytest.mark.parametrize(
    "start",
    [{"cough"}, {"abdominal_pain"}, {"headache"}, {"fatigue"}, {"limb_or_joint_pain"}, set()],
)
def test_never_more_than_six_counted_questions(start: set[str]) -> None:
    for answers in ({}, {"fever", "cough", "breathless"}):
        knowns, asked = interview(start, set(answers))
        assert sum(counted for _, counted in asked) <= MAX_QUESTIONS
        assert len({c for c, _ in asked}) == len(asked)  # never the same question twice


def test_the_floor_is_never_lowered() -> None:
    knowns, _ = interview({"runny_nose", "sore_throat"}, floor=Urgency.URGENT_24H)
    assert decide(KB, knowns).urgency is Urgency.URGENT_24H
    knowns, _ = interview({"runny_nose"}, floor=Urgency.EMERGENCY)
    assert decide(KB, knowns).urgency is Urgency.EMERGENCY


# ----- safeguard 2: confidence is not a diagnosis (NAV-2) -----------------------------------------


def test_low_confidence_means_a_general_physician_first() -> None:
    knowns = Knowns(present=frozenset({"fatigue", "headache"}), counted=MAX_QUESTIONS, age_years=40)
    decision = decide(KB, knowns)
    if decision.confidence < 0.75:
        assert decision.low_confidence and decision.primary is Specialty.GENERAL_MEDICINE
        assert len(decision.alternatives) <= 3
        assert Specialty.GENERAL_MEDICINE not in {o.specialty for o in decision.alternatives}


def test_a_confident_answer_names_the_doctor_type() -> None:
    knowns, _ = interview({"chest_pain"}, {"pain_spreading", "sweating"}, age=58, sex="male")
    decision = decide(KB, knowns)
    assert (decision.primary, decision.low_confidence) == (Specialty.CARDIOLOGY, False)
    assert decision.confidence >= 0.75


def test_it_stops_when_confident_but_only_after_the_red_flag_follow_ups() -> None:
    knowns = Knowns(present=frozenset({"ear_pain", "fever", "runny_nose"}), age_years=30)
    spec, urg = distributions(KB, posterior(KB, knowns))
    assert max(spec.values()) >= 0.75 and max(urg.values()) >= 0.8  # already confident
    ask = next_question(KB, knowns)
    assert ask is not None and ask.red_flag_screen  # fever: breathing and neck first (NAV-1)
    screened = replace(knowns, absent=frozenset({"breathless", "stiff_neck"}))
    assert next_question(KB, screened) is None


def test_nothing_scored_never_decides_from_the_prior_alone() -> None:
    decision = decide(KB, Knowns(age_years=40))
    assert (decision.urgency, decision.primary, decision.low_confidence) == (
        Urgency.ROUTINE,
        Specialty.GENERAL_MEDICINE,
        True,
    )


# ----- step 7: sooner care ------------------------------------------------------------------------


def test_a_likely_more_urgent_window_wins() -> None:
    knowns, _ = interview({"chest_pain"}, age=30)
    _, urg = distributions(KB, posterior(KB, knowns))
    decision = decide(KB, knowns)
    for window, p in urg.items():
        if p >= 0.2:
            assert decision.urgency.rank >= window.rank


# ----- the ambiguity check, children and complaints -----------------------------------------------


def test_dizzy_is_clarified_before_anything_else() -> None:
    ask = next_question(KB, Knowns(present=frozenset({"dizzy"}), age_years=70))
    assert ask is not None and ask.clarify == "dizzy_kind"


def test_a_child_sees_a_childrens_doctor_first() -> None:
    knowns, _ = interview({"ear_pain"}, {"fever", "runny_nose"}, age=6)
    decision = decide(KB, knowns)
    assert decision.primary is Specialty.PAEDIATRICS
    assert Specialty.ENT in {o.specialty for o in decision.alternatives}


@pytest.mark.parametrize(
    ("complaint", "specialties", "urgency"),
    [
        ("high_sugar", (Specialty.GENERAL_MEDICINE, Specialty.ENDOCRINOLOGY), Urgency.SOON_72H),
        ("urinary_symptoms", (Specialty.GENERAL_MEDICINE, Specialty.UROLOGY), Urgency.SOON_72H),
        ("memory_concern", (Specialty.GENERAL_MEDICINE, Specialty.NEUROLOGY), Urgency.ROUTINE),
        ("vertigo", (Specialty.GENERAL_MEDICINE, Specialty.ENT), Urgency.SOON_72H),
    ],
)
def test_reviewed_complaint_rules_route_elder_care_complaints(
    complaint: str, specialties: tuple[Specialty, ...], urgency: Urgency
) -> None:
    knowns = Knowns(present=frozenset({complaint}), age_years=72)
    assert next_question(KB, knowns) is None  # nothing scored to refine
    decision = decide(KB, knowns)
    assert decision.primary is specialties[0]
    assert specialties[1] in {o.specialty for o in decision.alternatives}
    assert decision.urgency is urgency


def test_not_sure_counts_as_asked_but_neither_yes_nor_no() -> None:
    knowns, asked = interview({"cough"}, unsure={c for c in KB.questions})
    assert knowns.absent == frozenset() and knowns.present == frozenset({"cough"})
    assert len(asked) == len(set(asked))


# ----- extraction and determinism -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "concept"),
    [
        ("I have a bad cough", "cough"),
        ("මට කැස්සක් තියෙනවා", "cough"),
        ("ugura ridenawa", "sore_throat"),
        ("මගේ සීනි වැඩිවෙලා", "high_sugar"),
        ("burning when passing urine", "urinary_symptoms"),
    ],
)
def test_words_become_concepts_in_three_scripts(text: str, concept: str) -> None:
    assert concept in KB.concepts_in(text)


def test_the_same_patient_gets_the_same_questions_and_result() -> None:
    runs = [interview({"cough", "fever"}, {"sputum", "breathless"}, age=50) for _ in range(3)]
    assert all(r[1] == runs[0][1] for r in runs)
    assert len({decide(KB, r[0]) for r in runs}) == 1


def test_every_combination_of_two_findings_terminates() -> None:
    symptoms = sorted(c for c in KB.scored if c in KB.questions)[:12]
    for pair in itertools.combinations(symptoms, 2):
        knowns, asked = interview(set(pair))
        assert sum(counted for _, counted in asked) <= MAX_QUESTIONS
