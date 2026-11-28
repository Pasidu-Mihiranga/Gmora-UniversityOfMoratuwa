"""Quiet hours in Sri Lanka time (spec 19 section 3)."""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest
from pydantic import ValidationError

from suwapath.modules.notifications.domain.quiet_hours import QuietHours, deliver_at

NIGHT = QuietHours(start=time(22, 0), end=time(6, 0))  # crosses midnight, 8 hours


def colombo(hour: int, minute: int = 0, day: int = 28) -> datetime:
    """A UTC instant for a Colombo wall-clock time (UTC+05:30)."""
    total = hour * 60 + minute - 330
    d, rest = divmod(total, 24 * 60)
    return datetime(2026, 11, day + d, rest // 60, rest % 60, tzinfo=UTC)


def test_outside_quiet_hours_it_goes_now() -> None:
    now = colombo(13, 0)
    assert deliver_at(now, "normal", NIGHT) == now


def test_late_evening_waits_until_the_morning() -> None:
    assert deliver_at(colombo(23, 15), "normal", NIGHT) == colombo(6, 0, day=29)


def test_after_midnight_waits_until_the_same_morning() -> None:
    assert deliver_at(colombo(2, 0), "normal", NIGHT) == colombo(6, 0)


def test_the_window_end_is_not_quiet() -> None:
    assert deliver_at(colombo(6, 0), "normal", NIGHT) == colombo(6, 0)


@pytest.mark.parametrize("urgency", ["urgent", "emergency"])
def test_urgent_and_emergency_always_pass(urgency: str) -> None:
    assert deliver_at(colombo(23, 15), urgency, NIGHT) == colombo(23, 15)


def test_no_setting_means_no_quiet_hours() -> None:
    assert deliver_at(colombo(23, 15), "normal", None) == colombo(23, 15)


def test_a_daytime_window_works_too() -> None:
    nap = QuietHours(start=time(13, 0), end=time(15, 0))
    assert deliver_at(colombo(14, 0), "normal", nap) == colombo(15, 0)


@pytest.mark.parametrize(("start", "end"), [(time(20, 0), time(7, 0)), (time(9, 0), time(9, 0))])
def test_more_than_10_hours_or_empty_is_refused(start: time, end: time) -> None:
    with pytest.raises(ValidationError):
        QuietHours(start=start, end=end)
