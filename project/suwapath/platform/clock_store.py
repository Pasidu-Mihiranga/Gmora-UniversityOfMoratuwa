"""Shared simulated clock (EVT-17): real time plus an offset stored in platform_clock, so every
process agrees. Development and demo only; production binds SystemClock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from suwapath.platform.models import ClockRow

CLOCK_CHANNEL = "suwapath_clock"


class SharedClock:
    def __init__(self) -> None:
        self._offset = timedelta(0)

    @property
    def offset(self) -> timedelta:
        return self._offset

    def now(self) -> datetime:
        return datetime.now(UTC) + self._offset  # noqa: TID251 (this is a Clock implementation)

    async def refresh(self, session: AsyncSession) -> None:
        seconds = (
            await session.execute(select(ClockRow.offset_seconds).where(ClockRow.id == 1))
        ).scalar()
        self._offset = timedelta(seconds=seconds or 0)

    async def advance(self, session: AsyncSession, delta: timedelta) -> datetime:
        """Moves the shared clock forward and notifies workers; the caller commits."""
        if delta <= timedelta(0):
            raise ValueError("the simulated clock only moves forward")
        await session.execute(
            update(ClockRow)
            .where(ClockRow.id == 1)
            .values(offset_seconds=ClockRow.offset_seconds + int(delta.total_seconds()))
        )
        await session.execute(text(f"NOTIFY {CLOCK_CHANNEL}"))
        await self.refresh(session)
        return self.now()
