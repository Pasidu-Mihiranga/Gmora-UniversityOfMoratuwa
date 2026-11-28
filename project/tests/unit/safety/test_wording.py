"""Banned wording (SAF-20, SAF-21): every pattern catches its example and lets its safe near miss
through, and the reviewed patient-facing templates pass."""

from __future__ import annotations

import pytest

from suwapath.contracts.enums import Language
from suwapath.modules.agents.application import companion_text
from suwapath.modules.safety.application.service import RuleSafetyService
from suwapath.modules.safety.domain.wording import BannedPattern

SAFETY = RuleSafetyService.from_content()
PATTERNS = [
    pytest.param(language, pattern, id=pattern.pattern_id)
    for language, patterns in SAFETY.wording.patterns.items()
    for pattern in patterns
]


@pytest.mark.parametrize(("language", "pattern"), PATTERNS)
def test_each_pattern_catches_its_example(language: Language, pattern: BannedPattern) -> None:
    hits = SAFETY.check_wording(pattern.example, language)
    assert pattern.pattern_id in {h.pattern_id for h in hits}


@pytest.mark.parametrize(("language", "pattern"), PATTERNS)
def test_each_safe_near_miss_passes(language: Language, pattern: BannedPattern) -> None:
    assert SAFETY.check_wording(pattern.near_miss, language) == []


def test_every_category_is_covered_in_both_languages() -> None:
    for language, patterns in SAFETY.wording.patterns.items():
        assert {p.category for p in patterns} == {
            "diagnosis",
            "reassurance",
            "dosing",
            "probability",
            "banned_term",
        }, language


@pytest.mark.parametrize(
    ("text", "language", "category"),
    [
        ("You might have a chest infection.", Language.EN, "diagnosis"),
        ("Don't worry, it is nothing serious.", Language.EN, "reassurance"),
        ("Increase your insulin tonight.", Language.EN, "dosing"),
        ("The model is 91 percent sure.", Language.EN, "probability"),
        ("Please follow our doctor's medical advice.", Language.EN, "banned_term"),
        ("Your scan shows a fracture.", Language.EN, "diagnosis"),
        ("ඔයාට ඩෙංගු හැදිලා.", Language.SI, "diagnosis"),
        ("ඔයා හොඳින් ඉන්නවා.", Language.SI, "reassurance"),
        ("ඉන්සියුලින් වැඩි කරන්න.", Language.SI, "dosing"),
        ("You have pneumonia.", Language.SI, "diagnosis"),  # English inside a Sinhala reply
    ],
)
def test_more_banned_examples(text: str, language: Language, category: str) -> None:
    assert category in {h.category for h in SAFETY.check_wording(text, language)}


@pytest.mark.parametrize(
    "text",
    [
        "This may be an emergency. Call 1990 now.",
        "Please see a doctor today.",
        "Your report was sent to your doctor for review.",
        "Do not change your medicines before you talk to your doctor.",
        "Have you taken your evening medicine?",
    ],
)
def test_safe_navigation_text_passes(text: str) -> None:
    assert SAFETY.check_wording(text, Language.EN) == []


def test_the_companion_templates_pass_the_wording_check() -> None:
    for key, language, text in companion_text.all_texts():
        assert SAFETY.check_wording(text, language) == [], (key, language.value, text)
