"""Reading a patient's answer to an intake question: Sinhala, Sinhala in Latin script, English."""

from __future__ import annotations

import pytest

from suwapath.modules.agents.domain.answers import Parsed, parse_answer


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("yes", "yes"),
        ("Yes, since morning", "yes"),
        ("ඔව්", "yes"),
        ("ow", "yes"),
        ("no", "no"),
        ("නැහැ", "no"),
        ("naha", "no"),
        ("not sure", "unsure"),
        ("I don't know", "unsure"),
        ("දන්නේ නැහැ", "unsure"),  # contains the word for "no" but means "not sure"
        ("danne naha", "unsure"),
        ("maybe", "unsure"),
    ],
)
def test_yes_no(text: str, expected: str) -> None:
    assert parse_answer(text, "yes_no") == Parsed(expected, expected)


@pytest.mark.parametrize("text", ["my knee hurts", "", "yes and no"])
def test_unclear_yes_no(text: str) -> None:
    assert parse_answer(text, "yes_no") is None


def test_numbers_and_durations() -> None:
    assert parse_answer("about 3 days", "duration") == Parsed("3 days", "3 days")
    assert parse_answer("දින 2", "duration") == Parsed("2 days", "2 days")
    assert parse_answer("38.5", "number") == Parsed("38.5", 38.5)
    assert parse_answer("high", "number") is None


def test_choices_match_whole_options() -> None:
    choices = ("morning", "evening")
    assert parse_answer("in the Evening", "choice", choices) == Parsed("evening", "evening")
    assert parse_answer("both", "choice", choices) is None
    assert parse_answer("not sure", "choice", choices) == Parsed("unsure", "unsure")
