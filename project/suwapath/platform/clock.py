"""The only place that reads the wall clock (EVT-16).

`SystemClock` in production. `OffsetClock` adds a forward-only offset for development and demo
(EVT-17); the shared, database-backed offset is wired in the event backbone (W0.5).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

COLOMBO = ZoneInfo("Asia/Colombo")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class OffsetClock:
    """Real time plus an offset that can only move forward."""

    def __init__(self, offset: timedelta = timedelta(0)) -> None:
        if offset < timedelta(0):
            raise ValueError("clock offset cannot be negative")
        self._offset = offset

    @property
    def offset(self) -> timedelta:
        return self._offset

    def now(self) -> datetime:
        return datetime.now(UTC) + self._offset

    def advance(self, delta: timedelta) -> datetime:
        if delta <= timedelta(0):
            raise ValueError("the simulated clock only moves forward")
        self._offset += delta
        return self.now()


class FixedClock:
    """Deterministic clock for tests."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        return self._at

    def advance(self, delta: timedelta) -> datetime:
        self._at += delta
        return self._at


def colombo_day_start(at: datetime) -> datetime:
    """Midnight in Asia/Colombo for the day containing `at`, as UTC (quota resets, CUS-9)."""
    local = at.astimezone(COLOMBO)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
