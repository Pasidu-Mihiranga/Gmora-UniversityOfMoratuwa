"""Dose times from a medicine's timing (CARE-2). Pure; Sri Lanka time; no I/O.

Default slot times: morning 08:00, noon 13:00, evening 19:00, night 22:00. Explicit times win
over slots. `days_of_week` uses Monday = 0. As-needed medicines have no scheduled doses.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from suwapath.contracts.enums import DoseSlot
from suwapath.contracts.fhir.resources import DoseTiming
from suwapath.platform.clock import COLOMBO

SLOT_TIMES: dict[DoseSlot, time] = {
    DoseSlot.MORNING: time(8, 0),
    DoseSlot.NOON: time(13, 0),
    DoseSlot.EVENING: time(19, 0),
    DoseSlot.NIGHT: time(22, 0),
}


def slot_for(at: time) -> DoseSlot:
    """The slot an explicit time belongs to, for summaries by time of day."""
    if at < time(11, 0):
        return DoseSlot.MORNING
    if at < time(16, 0):
        return DoseSlot.NOON
    if at < time(21, 0):
        return DoseSlot.EVENING
    return DoseSlot.NIGHT


def doses_between(
    timing: DoseTiming, start: datetime, end: datetime
) -> list[tuple[datetime, DoseSlot]]:
    """Every dose in [start, end), as UTC instants with their slot, in time order."""
    if timing.as_needed:
        return []
    moments = (
        [(t, slot_for(t)) for t in timing.times]
        if timing.times
        else [(SLOT_TIMES[s], s) for s in timing.slots]
    )
    out: list[tuple[datetime, DoseSlot]] = []
    day: date = start.astimezone(COLOMBO).date()
    last: date = end.astimezone(COLOMBO).date()
    while day <= last:
        if day.weekday() in timing.days_of_week:
            for at, slot in moments:
                instant = datetime.combine(day, at, tzinfo=COLOMBO).astimezone(UTC)
                if start <= instant < end:
                    out.append((instant, slot))
        day += timedelta(days=1)
    return sorted(set(out))


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """A Sri Lanka calendar day as a UTC interval."""
    begin = datetime.combine(day, time(0, 0), tzinfo=COLOMBO).astimezone(UTC)
    return begin, begin + timedelta(days=1)
