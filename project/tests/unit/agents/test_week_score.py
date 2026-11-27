"""The guardian week score: rule thresholds, when Jev is asked, and how its answer is used."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from suwapath.contracts.enums import DoseSlot
from suwapath.contracts.ids import PatientId
from suwapath.contracts.ports.care import AdherenceDay, AdherenceSummary
from suwapath.modules.agents.domain.week_score import (
    WeekScore,
    combine,
    decision_state,
    needs_judgement,
    rules_score,
)

TODAY = date(2026, 11, 28)


def week(*missed: int, taken: int = 2) -> AdherenceSummary:
    """`missed` per day, oldest first; the last value is today."""
    days = tuple(
        AdherenceDay(
            day=TODAY - timedelta(days=len(missed) - 1 - n),
            taken=taken,
            missed=m,
            pending=0,
            missed_slots=(DoseSlot.EVENING,) * min(m, 1),
        )
        for n, m in enumerate(missed)
    )
    return AdherenceSummary(
        patient_id=PatientId(uuid4()),
        days=days,
        missed_by_slot={DoseSlot.EVENING: sum(missed)},
        current_streak_days=0,
    )


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (week(0, 0, 0, 0, 0, 0, 0), WeekScore.STABLE),
        (week(1, 0, 0, 0, 0, 0, 0), WeekScore.STABLE),  # one miss a week ago, then fine
        (week(0, 0, 0, 0, 0, 0, 1), WeekScore.WATCH),
        (week(1, 0, 1, 0, 0, 0, 0), WeekScore.WATCH),  # 2 in the week, none recent
        (week(0, 0, 0, 0, 0, 1, 1), WeekScore.CONCERNING),  # 2 in 48 hours
        (week(1, 1, 1, 1, 0, 0, 0), WeekScore.CONCERNING),  # 4 in the week
        (week(0, 0, 0, 0, 0, 1, 1, taken=0), WeekScore.URGENT),  # nothing taken for 2 days
    ],
)
def test_rule_thresholds(summary: AdherenceSummary, expected: WeekScore) -> None:
    assert rules_score(summary) is expected


def test_two_quiet_days_with_nothing_due_are_not_urgent() -> None:
    assert rules_score(week(0, 0, 0, 0, 0, 0, 0, taken=0)) is WeekScore.STABLE


def test_only_the_unclear_middle_goes_to_the_model() -> None:
    assert [s for s in WeekScore if needs_judgement(s)] == [WeekScore.WATCH, WeekScore.CONCERNING]


def test_a_confident_model_decides_the_middle() -> None:
    assert combine(WeekScore.CONCERNING, "watch", 0.8) == (WeekScore.WATCH, "jev")
    assert combine(WeekScore.WATCH, "urgent", 0.9) == (WeekScore.URGENT, "jev")


@pytest.mark.parametrize(("choice", "confidence"), [("watch", 0.4), (None, 0.0), ("maybe", 0.9)])
def test_unsure_or_unknown_answers_keep_the_rules(choice: str | None, confidence: float) -> None:
    assert combine(WeekScore.CONCERNING, choice, confidence) == (WeekScore.CONCERNING, "rules")


def test_the_model_sees_counts_and_times_only() -> None:
    summary = week(0, 0, 0, 0, 0, 1, 1)
    state = "\n".join(decision_state(summary))
    assert "Day 7: taken 2, missed 1" in state
    assert "evening" in state
    assert str(summary.patient_id) not in state
    assert TODAY.isoformat() not in state
