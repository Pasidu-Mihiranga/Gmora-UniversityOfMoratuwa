"""How serious a patient's medicine week looks (spec 05 section 4, guardian agent).

Rules decide the clear cases at no cost; Jev decides the unclear middle, where reading the pattern
beats counting (GW-13, KNW-1 tiers). The thresholds are product defaults agreed for the MVP, not
clinical facts; the monitoring suite (spec 13) measures false alerts and delay against them.
"""

from __future__ import annotations

from enum import StrEnum

from suwapath.contracts.ports.care import AdherenceSummary


class WeekScore(StrEnum):
    STABLE = "stable"
    WATCH = "watch"
    CONCERNING = "concerning"
    URGENT = "urgent"

    @property
    def rank(self) -> int:
        return list(WeekScore).index(self)


# Product defaults (agreed 27 Nov 2026), not clinical facts.
URGENT_NOTHING_TAKEN_MISSED = 2  # nothing taken in the last 2 days, at least this many missed
CONCERNING_RECENT = 2  # missed in the last 2 days
CONCERNING_WEEK = 4  # missed in 7 days
WATCH_RECENT = 1
WATCH_WEEK = 2
JEV_MIN_CONFIDENCE = 0.6

CRITERIA: dict[WeekScore, str] = {
    WeekScore.STABLE: "Doses are being taken; any miss looks like a one-off.",
    WeekScore.WATCH: "A few misses without a clear pattern; keep an eye on it, no contact needed.",
    WeekScore.CONCERNING: "A pattern of misses (for example the same time of day, or rising); "
    "someone should check on the patient.",
    WeekScore.URGENT: "Doses have stopped almost completely; the family should act today.",
}
INSTRUCTIONS = (
    "You see one patient's medicine adherence for the last days: doses taken, missed and pending "
    "per day, and which times of day were missed. Rate how the week looks for a family member who "
    "watches over the patient. Judge the pattern, not single numbers. Do not give medical advice."
)


def rules_score(summary: AdherenceSummary) -> WeekScore:
    days = sorted(summary.days, key=lambda d: d.day)
    recent = days[-2:]
    recent_missed = sum(d.missed for d in recent)
    recent_taken = sum(d.taken for d in recent)
    week_missed = sum(d.missed for d in days[-7:])
    if recent_taken == 0 and recent_missed >= URGENT_NOTHING_TAKEN_MISSED:
        return WeekScore.URGENT
    if recent_missed >= CONCERNING_RECENT or week_missed >= CONCERNING_WEEK:
        return WeekScore.CONCERNING
    if recent_missed >= WATCH_RECENT or week_missed >= WATCH_WEEK:
        return WeekScore.WATCH
    return WeekScore.STABLE


def needs_judgement(score: WeekScore) -> bool:
    """Only the middle is worth a model call; clear weeks are decided by the rules for free.
    An urgent week is never sent to the model, so it can never be talked down."""
    return score in (WeekScore.WATCH, WeekScore.CONCERNING)


def decision_state(summary: AdherenceSummary) -> list[str]:
    """Counts and times of day only: no names, medicines or identifiers leave the system."""
    lines = []
    for n, day in enumerate(sorted(summary.days, key=lambda d: d.day), start=1):
        slots = ", ".join(s.value for s in day.missed_slots) or "none"
        lines.append(
            f"Day {n}: taken {day.taken}, missed {day.missed}, pending {day.pending}, "
            f"missed at: {slots}"
        )
    by_slot = ", ".join(f"{s.value} {n}" for s, n in sorted(summary.missed_by_slot.items()))
    lines.append(f"Missed by time of day this week: {by_slot or 'none'}")
    lines.append(f"Current streak of full days: {summary.current_streak_days}")
    return lines


def combine(rules: WeekScore, choice: str | None, confidence: float) -> tuple[WeekScore, str]:
    """The final score and who decided it (`rules` or `jev`)."""
    if choice is None or confidence < JEV_MIN_CONFIDENCE:
        return rules, "rules"
    try:
        return WeekScore(choice), "jev"
    except ValueError:
        return rules, "rules"
