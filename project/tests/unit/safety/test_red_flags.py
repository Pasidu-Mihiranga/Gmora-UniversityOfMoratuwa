"""Red-flag rules v1 (spec 07 section 3): every rule fires on its pattern and stays quiet on a near
miss, in English, Sinhala and Sinhala in Latin letters; the floor is the highest fired urgency."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import Language, Urgency
from suwapath.contracts.ports.safety import PatientContext, SafetyAssessment
from suwapath.modules.safety.application.service import RuleSafetyService
from suwapath.modules.safety.domain.lexicon import SCRIPTS
from suwapath.modules.safety.domain.rules import RULES

SAFETY = RuleSafetyService.from_content()
ADULT: dict[str, Any] = {"age_years": 35}


def assess(text: str, **context: Any) -> SafetyAssessment:
    return SAFETY.assess(
        UntrustedText(value=text, source="patient_message"), PatientContext(**context)
    )


def fired(text: str, **context: Any) -> set[str]:
    return {m.rule_id for m in assess(text, **context).red_flags}


@dataclass
class Case:
    rule_id: str
    fires: dict[str, str]  # script -> text
    near_miss: dict[str, str]  # script -> text that must not fire this rule
    context: dict[str, Any] = field(default_factory=lambda: dict(ADULT))
    near_context: dict[str, Any] | None = None  # context for the near misses, if different


CASES = [
    Case(
        "RF-CARD-001",
        {
            "en": "I have chest pain and I am sweating a lot",
            "si": "පපුවේ කැක්කුමක්, දහඩිය දානවා",
            "si_latn": "papuwe kakkuma wam athata yanawa",
        },
        {"en": "I have chest pain when I press on it", "si": "පපුවේ කැක්කුමක් තියෙනවා"},
    ),
    Case(
        "RF-CARD-002",
        {"en": "My chest hurts", "si": "පපුව රිදෙනවා", "si_latn": "papuwa ridenawa"},
        {"en": "My chest hurts", "si": "පපුව රිදෙනවා"},
        context={"age_years": 58},
        near_context={"age_years": 30},
    ),
    Case(
        "RF-CARD-002",
        {"en": "I have chest pressure", "si": "පපුවේ තදයක්"},
        {"en": "I have back pain", "si": "කොන්දේ කැක්කුම"},
        context={"age_years": 40, "chronic_codes": frozenset({"diabetes_type2"})},
    ),
    Case(
        "RF-CARD-003",
        {"en": "I fainted this morning", "si": "අද උදේ ක්ලාන්ත වුණා", "si_latn": "ada ude klantha wuna"},
        {"en": "I feel tired", "si": "මට මහන්සියි"},
    ),
    Case(
        "RF-NEURO-001",
        {
            "en": "Her face is drooping on one side",
            "si": "අම්මාගේ කට ඇදවෙලා",
            "si_latn": "ammage kata adawela",
        },
        {"en": "My face is itchy", "si": "මුහුණ කසනවා"},
    ),
    Case(
        "RF-NEURO-001",
        {"en": "Suddenly his speech is slurred", "si": "කතාව පැටලෙනවා"},
        {"en": "He speaks slowly when tired", "si": "හෙමින් කතා කරනවා"},
    ),
    Case(
        "RF-NEURO-002",
        {
            "en": "I have fever and a stiff neck",
            "si": "උණ සහ බෙල්ල තද",
            "si_latn": "una saha bella thada",
        },
        {"en": "I have fever and a sore throat", "si": "උණයි, උගුර රිදෙනවා"},
    ),
    Case(
        "RF-NEURO-003",
        {
            "en": "This is the worst headache of my life",
            "si": "ජීවිතේ නරකම හිසරදය",
            "si_latn": "hadisiye daruna hisaradaya",
        },
        {"en": "I have a mild headache", "si": "පොඩි හිසරදයක්"},
    ),
    Case(
        "RF-NEURO-004",
        {"en": "He is having a fit", "si": "වලිප්පුවක් හැදුණා", "si_latn": "walippuwak haduna"},
        {"en": "I feel fit and well", "si": "ඇඟ වෙව්ලනවා"},
    ),
    Case(
        "RF-RESP-001",
        {"en": "I can't breathe", "si": "හුස්ම ගන්න බෑ", "si_latn": "husma ganna ba"},
        {"en": "I get a bit out of breath on the stairs", "si": "පඩි නගිද්දී හති වැටෙනවා"},
    ),
    Case(
        "RF-RESP-002",
        {"en": "I am coughing up blood", "si": "කැස්සේ ලේ එනවා", "si_latn": "kesse le enawa"},
        {"en": "I have a cough", "si": "කැස්ස තියෙනවා"},
    ),
    Case(
        "RF-RESP-003",
        {
            "en": "I have fever and shortness of breath",
            "si": "උණ සහ හුස්ම ගන්න අමාරුයි",
            "si_latn": "una saha husma ganna amaruyi",
        },
        {"en": "I have fever and a runny nose", "si": "උණ සහ හෙම්බිරිස්සාව"},
    ),
    Case(
        "RF-BLEED-001",
        {"en": "The bleeding will not stop", "si": "ලේ නවතින්නේ නැහැ", "si_latn": "le nawathinne na"},
        {"en": "A small cut, the bleeding stopped", "si": "පොඩි තුවාලයක්, ලේ නැවතුණා"},
    ),
    Case(
        "RF-GI-001",
        {"en": "I vomited blood", "si": "ලේ වමනය", "si_latn": "le wamane giya"},
        {"en": "I vomited after lunch", "si": "කෑවට පස්සේ වමනය"},
    ),
    Case(
        "RF-GI-001",
        {"en": "I have black stools since yesterday", "si": "කළු පාට මළ යනවා"},
        {"en": "My stool is hard", "si": "මළ තද වෙලා"},
    ),
    Case(
        "RF-GI-002",
        {
            "en": "I have severe stomach pain",
            "si": "බඩේ දරුණු කැක්කුමක්",
            "si_latn": "bade daruna kakkuma",
        },
        {"en": "I have a mild stomach ache", "si": "පොඩි බඩේ කැක්කුමක්"},
    ),
    Case(
        "RF-MAT-001",
        {"en": "I am bleeding", "si": "ලේ යනවා", "si_latn": "le yanawa"},
        {"en": "I am bleeding", "si": "ලේ යනවා"},
        context={"age_years": 28, "sex": "female", "pregnant": True},
        near_context={"age_years": 28, "sex": "female"},
    ),
    Case(
        "RF-MAT-001",
        {"en": "I am 20 weeks pregnant and spotting", "si": "මම ගැබිනියි, ලේ යනවා"},
        {"en": "I am 20 weeks pregnant and tired", "si": "මම ගැබිනියි, මහන්සියි"},
    ),
    Case(
        "RF-MAT-002",
        {
            "en": "I have a headache and blurred vision",
            "si": "හිසරදය, ඇස් බොඳ",
            "si_latn": "as bonda",
        },
        {"en": "I have a headache and blurred vision", "si": "හිසරදය, ඇස් බොඳ"},
        context={"age_years": 30, "sex": "female", "pregnant": True},
        near_context={"age_years": 30, "sex": "female"},
    ),
    Case(
        "RF-MAT-003",
        {"en": "The baby is not moving", "si": "බබා සෙලවෙන්නේ නැහැ", "si_latn": "baba selawenne na"},
        {"en": "The baby is kicking a lot", "si": "බබා ගොඩක් පයින් ගහනවා"},
        context={"age_years": 30, "sex": "female", "pregnant": True},
    ),
    Case(
        "RF-MAT-004",
        {"en": "My waters broke", "si": "වතුර ගියා", "si_latn": "wathura giya"},
        {"en": "I drank a lot of water", "si": "වතුර ගොඩක් බිව්වා"},
        context={"age_years": 30, "sex": "female", "pregnant": True},
    ),
    Case(
        "RF-MAT-005",
        {"en": "I have a fever", "si": "උණ තියෙනවා", "si_latn": "una thiyenawa"},
        {"en": "I have a fever", "si": "උණ තියෙනවා"},
        context={"age_years": 30, "sex": "female", "postpartum": True},
        near_context={"age_years": 30, "sex": "female"},
    ),
    Case(
        "RF-MH-001",
        {"en": "I want to die", "si": "මැරෙන්න හිතෙනවා", "si_latn": "marenna hithenawa"},
        {"en": "I am dying to see my grandson", "si": "මට දුකයි"},
    ),
    Case(
        "RF-PAED-001",
        {
            "en": "He has a fever and is not feeding",
            "si": "කිරි බොන්නේ නැහැ",
            "si_latn": "kiri bonne na",
        },
        {"en": "He has a runny nose", "si": "හෙම්බිරිස්සාව"},
        context={"age_years": 0},
    ),
    Case(
        "RF-PAED-001",
        {"en": "My baby has a high fever", "si": "බබාට උණ", "si_latn": "babata una"},
        {"en": "I have a high fever", "si": "මට උණ"},
    ),
    Case(
        "RF-GER-001",
        {
            "en": "I fell and cannot get up",
            "si": "බිම වැටුණා, නැගිටින්න බෑ",
            "si_latn": "bima watuna nagitinna ba",
        },
        {"en": "I fell asleep early", "si": "බිම වැටුණා, නැගිටින්න බෑ"},
        context={"age_years": 75},
        near_context={"age_years": 40},
    ),
    Case(
        "RF-GER-002",
        {"en": "Amma is confused today", "si": "අම්මා අද ව්‍යාකූලයි", "si_latn": "amma ada wyakulayi"},
        {"en": "I am confused about my appointment", "si": "මට වේලාව ගැන ව්‍යාකූලයි"},
        context={"age_years": 80},
        near_context={"age_years": 30},
    ),
    Case(
        "RF-GER-003",
        {"en": "I feel dizzy", "si": "ඔලුව කැරකෙනවා", "si_latn": "oluwa karakenawa"},
        {"en": "I feel dizzy", "si": "ඔලුව කැරකෙනවා"},
        context={"age_years": 70, "missed_doses_48h": 2},
        near_context={"age_years": 70},
    ),
    Case(
        "RF-GER-003",
        {"en": "I am dizzy, I forgot my tablets", "si": "කරකැවිල්ල, බෙහෙත් බොන්න අමතක වුණා"},
        {"en": "I am dizzy, I forgot my tablets", "si": "කරකැවිල්ල, බෙහෙත් බොන්න අමතක වුණා"},
        context={"age_years": 70},
        near_context={"age_years": 50},
    ),
    Case(
        "RF-MET-001",
        {
            "en": "I am sweating and shaking",
            "si": "දහඩිය දානවා, වෙව්ලනවා",
            "si_latn": "dahadiya danawa wewlanawa",
        },
        {"en": "I am sweating and shaking", "si": "දහඩිය දානවා, වෙව්ලනවා"},
        context={"age_years": 66, "chronic_codes": frozenset({"diabetes_type2"})},
        near_context={"age_years": 66},
    ),
    Case(
        "RF-MET-001",
        {"en": "My sugar dropped", "si": "සීනි අඩුවෙලා", "si_latn": "seeni aduwela"},
        {"en": "I am sweating after my walk", "si": "මම සීනි අඩු කරනවා"},
        context={"age_years": 66, "chronic_codes": frozenset({"diabetes_type2"})},
    ),
    Case(
        "RF-INF-001",
        {
            "en": "I have had a fever for 3 days",
            "si": "දවස් තුනක ඉඳන් උණ",
            "si_latn": "dawas thunak idan una",
        },
        {"en": "Fever since this morning", "si": "අද උදේ ඉඳන් උණ"},
    ),
]

FIRING = [
    pytest.param(case.rule_id, script, text, case.context, id=f"{case.rule_id}-{script}-{n}")
    for n, case in enumerate(CASES)
    for script, text in case.fires.items()
]
NEAR_MISSES = [
    pytest.param(
        case.rule_id,
        text,
        case.near_context if case.near_context is not None else case.context,
        id=f"{case.rule_id}-{script}-{n}",
    )
    for n, case in enumerate(CASES)
    for script, text in case.near_miss.items()
]


@pytest.mark.parametrize(("rule_id", "script", "text", "context"), FIRING)
def test_the_rule_fires(rule_id: str, script: str, text: str, context: dict[str, Any]) -> None:
    assert rule_id in fired(text, **context)


@pytest.mark.parametrize(("rule_id", "text", "context"), NEAR_MISSES)
def test_the_near_miss_does_not_fire(rule_id: str, text: str, context: dict[str, Any]) -> None:
    assert rule_id not in fired(text, **context)


def test_every_rule_has_firing_and_near_miss_cases_in_sinhala_and_english() -> None:
    for rule in RULES:
        cases = [c for c in CASES if c.rule_id == rule.rule_id]
        assert cases, rule.rule_id
        for language in ("en", "si"):
            assert any(language in c.fires for c in cases), (rule.rule_id, language)
            assert any(language in c.near_miss for c in cases), (rule.rule_id, language)


def test_the_rule_set_is_v1_from_the_spec() -> None:
    ids = {rule.rule_id for rule in RULES}
    assert len(ids) == len(RULES) == 25
    assert {f"RF-MAT-00{n}" for n in range(1, 6)} <= ids
    assert {"RF-GER-003", "RF-MET-001", "RF-INF-001", "RF-MH-001", "RF-PAED-001"} <= ids


def test_every_rule_concept_is_in_the_lexicon_in_all_three_scripts() -> None:
    phrases = SAFETY.lexicon.phrases
    for rule in RULES:
        for concept in rule.concepts():
            assert concept in phrases, (rule.rule_id, concept)
            for script in SCRIPTS:
                assert phrases[concept][script], (concept, script)


# ----- the lexicon ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "I am unable to sleep",  # "una" (fever, Sinhala in Latin letters) inside a word
        "උණුසුම් වතුර බොන්න කැමතියි",  # "උණ" (fever) inside "warm"
        "My grandson is fine",
        "",
    ],
)
def test_words_inside_other_words_are_not_concepts(text: str) -> None:
    assert "fever" not in assess(text).concepts


def test_case_punctuation_and_joiners_do_not_matter() -> None:
    assert "chest_pain" in assess("CHEST-PAIN!!").concepts
    assert "confusion" in assess("ව්‍යාකූලයි").concepts  # with and without the joiner
    assert "confusion" in assess("ව්යාකූලයි").concepts


def test_mixed_scripts_in_one_message() -> None:
    assert {"chest_pain", "sweating"} <= assess("papuwa ridenawa, දහඩිය දානවා").concepts


# ----- the floor (SAF-3) ------------------------------------------------------------------------


def test_the_floor_is_the_highest_fired_urgency() -> None:
    result = assess("I have been coughing up blood and I fainted")
    assert {m.urgency for m in result.red_flags} == {Urgency.URGENT_24H, Urgency.EMERGENCY}
    assert result.urgency_floor is Urgency.EMERGENCY and result.emergency
    assert result.help_lines == ("1990",)


def test_an_urgent_flag_is_not_an_emergency() -> None:
    result = assess("I am coughing up blood")
    assert result.urgency_floor is Urgency.URGENT_24H
    assert not result.emergency and result.help_lines == ()


def test_no_flag_means_no_floor() -> None:
    result = assess("I have a mild cough and a runny nose")
    assert result.red_flags == () and result.urgency_floor is None and not result.emergency


def test_self_harm_shows_the_mental_health_line_too() -> None:
    assert assess("I want to end my life").help_lines == ("1990", "1926")


def test_the_rules_version_is_recorded() -> None:
    assert assess("hello").rules_version.startswith("rules-v1+lexicon-v1")


def test_the_matched_concepts_are_named() -> None:
    match = next(
        m for m in assess("chest pain and sweating").red_flags if m.rule_id == "RF-CARD-001"
    )
    assert match.matched_concepts == ("chest_pain", "sweating")
    assert match.label_key == "red_flag.RF-CARD-001"


def test_the_language_hint_does_not_narrow_the_scripts() -> None:
    text = UntrustedText(value="papuwa ridenawa saha dahadiya", source="patient_message")
    result = SAFETY.assess(text, PatientContext(**ADULT), Language.EN)
    assert "RF-CARD-001" in {m.rule_id for m in result.red_flags}


def test_it_is_fast_on_a_long_message() -> None:
    text = UntrustedText(
        value=("I have a mild cough today. " * 80)[:2000], source="patient_message"
    )
    started = time.perf_counter()
    for _ in range(10):
        SAFETY.assess(text, PatientContext(**ADULT))
    assert (time.perf_counter() - started) / 10 < 0.05  # spec 13: under 50 ms
