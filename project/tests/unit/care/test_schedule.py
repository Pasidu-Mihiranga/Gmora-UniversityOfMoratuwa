"""CARE-2: dose times from timing, on Sri Lanka time."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from suwapath.contracts.enums import DoseSlot
from suwapath.contracts.fhir.resources import DoseTiming
from suwapath.modules.care.domain.schedule import day_bounds, doses_between

# 2026-11-28 is a Saturday. Sri Lanka is UTC+05:30.
START = datetime(2026, 11, 28, 0, 0, tzinfo=UTC)  # 05:30 in Colombo


def test_slots_become_sri_lanka_times() -> None:
    timing = DoseTiming(slots=[DoseSlot.MORNING, DoseSlot.EVENING])
    doses = doses_between(timing, START, START + timedelta(days=1))
    assert [d.strftime("%Y-%m-%d %H:%M") for d, _ in doses] == [
        "2026-11-28 02:30",  # 08:00 Colombo
        "2026-11-28 13:30",  # 19:00 Colombo
    ]
    assert [s for _, s in doses] == [DoseSlot.MORNING, DoseSlot.EVENING]


def test_explicit_times_win_and_get_a_slot() -> None:
    timing = DoseTiming(slots=[DoseSlot.MORNING], times=[time(21, 30)])
    doses = doses_between(timing, START, START + timedelta(days=1))
    assert [s for _, s in doses] == [DoseSlot.NIGHT]


def test_weekdays_are_respected_and_as_needed_has_no_doses() -> None:
    weekdays = DoseTiming(slots=[DoseSlot.MORNING], days_of_week=[0, 1, 2, 3, 4])
    assert doses_between(weekdays, START, START + timedelta(days=2)) == []  # Saturday, Sunday
    assert len(doses_between(weekdays, START, START + timedelta(days=3))) == 1  # Monday
    assert (
        doses_between(
            DoseTiming(slots=[DoseSlot.NOON], as_needed=True), START, START + timedelta(days=7)
        )
        == []
    )


def test_the_window_is_half_open_and_48_hours_gives_two_days() -> None:
    timing = DoseTiming(slots=list(DoseSlot))
    doses = doses_between(timing, START, START + timedelta(hours=48))
    assert len(doses) == 8 and all(START <= d < START + timedelta(hours=48) for d, _ in doses)


def test_a_day_is_a_sri_lanka_calendar_day() -> None:
    begin, end = day_bounds(date(2026, 11, 28))
    assert begin == datetime(2026, 11, 27, 18, 30, tzinfo=UTC) and end - begin == timedelta(days=1)
