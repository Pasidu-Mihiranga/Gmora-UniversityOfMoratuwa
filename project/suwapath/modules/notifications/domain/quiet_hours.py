"""Quiet hours (spec 19 section 3): a window in Sri Lanka time, at most 10 hours, that may cross
midnight. Normal notifications wait until it ends; urgent and emergency ones always pass."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from pydantic import model_validator

from suwapath.contracts.common import Contract
from suwapath.platform.clock import COLOMBO

MAX_QUIET = timedelta(hours=10)


class QuietHours(Contract):
    start: time
    end: time

    @model_validator(mode="after")
    def _bounded(self) -> QuietHours:
        if self.start == self.end or self.length() > MAX_QUIET:
            raise ValueError("quiet hours are 1 minute to 10 hours")
        return self

    def length(self) -> timedelta:
        start = timedelta(hours=self.start.hour, minutes=self.start.minute)
        end = timedelta(hours=self.end.hour, minutes=self.end.minute)
        return (end - start) % timedelta(days=1)


def deliver_at(now: datetime, urgency: str, quiet: QuietHours | None) -> datetime:
    """When a notification may reach the person: now, or the end of the current quiet window."""
    if quiet is None or urgency in ("urgent", "emergency"):
        return now
    local = now.astimezone(COLOMBO)
    for day in (local.date() - timedelta(days=1), local.date()):  # a window may start yesterday
        start = datetime.combine(day, quiet.start, COLOMBO)
        end = start + quiet.length()
        if start <= local < end:
            return end.astimezone(now.tzinfo)
    return now
