"""Fire due timers (EVT-14, EVT-15): mark fired and publish platform.timer_fired atomically."""

from __future__ import annotations

from sqlalchemy import select

from suwapath.contracts.events.catalog import TimerFired
from suwapath.contracts.ids import TimerId
from suwapath.platform.models import TimerRow
from suwapath.platform.tracing import set_trace_id
from suwapath.platform.uow import UnitOfWorkFactory


async def fire_due_timers(uow_factory: UnitOfWorkFactory, batch: int = 100) -> int:
    async with uow_factory() as uow:
        now = uow.clock.now()
        timers = (
            (
                await uow.session.execute(
                    select(TimerRow)
                    .where(TimerRow.status == "scheduled", TimerRow.due_at <= now)
                    .order_by(TimerRow.due_at)
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for timer in timers:
            if isinstance(timer.payload.get("trace_id"), str):
                set_trace_id(
                    timer.payload["trace_id"]
                )  # keep the workflow's trace (spec 20, AUD-2)
            timer.status = "fired"
            timer.fired_at = now
            uow.publish(
                TimerFired(
                    timer_id=TimerId(timer.id),
                    kind=timer.kind,
                    ref_type=timer.ref_type,
                    ref_id=timer.ref_id,
                ),
                aggregate_id=str(timer.id),
            )
        await uow.commit()
        return len(timers)
