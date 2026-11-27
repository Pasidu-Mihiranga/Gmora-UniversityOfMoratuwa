"""Invariant 1 on the companion's fixed text: no diagnosis, reassurance or dosing; si and en."""

from __future__ import annotations

import re

import pytest

from suwapath.contracts.enums import Language, Specialty, Urgency
from suwapath.contracts.suggestions import MAX_LABEL_CHARS
from suwapath.contracts.testing.doubles_clinical import KeywordSafetyService
from suwapath.modules.agents.application import companion_text as copy

TEXTS = copy.all_texts()
BANNED = re.compile(
    r"you have (a|an)\b|you are fine|nothing to worry|don't worry|take \d|mg\b|tablets? a day",
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    ("key", "language", "text"), TEXTS, ids=[f"{k}-{lang}" for k, lang, _ in TEXTS]
)
def test_no_diagnosis_reassurance_or_dosing(key: str, language: Language, text: str) -> None:
    assert not KeywordSafetyService().check_wording(text, language), key
    assert not BANNED.search(text), key


def test_every_template_has_both_languages() -> None:
    for table in (copy.TEXT, copy.QUESTIONS, copy.CHIPS):
        for key, texts in table.items():
            assert set(texts) == {Language.EN, Language.SI}, key
    assert set(copy.URGENCY) == set(Urgency)


def test_urgency_wording_is_the_spec_wording() -> None:
    assert (
        copy.URGENCY[Urgency.EMERGENCY][Language.EN] == "This may be an emergency. Call 1990 now."
    )
    assert copy.URGENCY[Urgency.URGENT_24H][Language.EN] == "Please see a doctor today."


def test_chips_fit() -> None:
    for key, texts in copy.CHIPS.items():
        for language in texts:
            assert len(copy.chip(key, language, time="28 Nov 10:30")) <= MAX_LABEL_CHARS


def test_unknown_specialties_and_questions_fall_back_to_plain_words() -> None:
    assert copy.specialty_name(Specialty.UROLOGY, Language.EN) == "urology"
    assert copy.question_text("q.unknown", "night_sweats", "yes_no", Language.EN) == (
        "Are you having night sweats?"
    )
