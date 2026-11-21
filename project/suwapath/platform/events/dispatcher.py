"""Outbox dispatcher (EVT-5 to EVT-8).

Two steps, each its own transaction:
1. fan_out: claim pending outbox rows (SKIP LOCKED) and create one inbox row per subscriber.
2. deliver: claim one due inbox row (SKIP LOCKED), run the handler in the same transaction and mark
   it done there, so the handler's database effects and the "done" mark commit together. A failure
   rolls both back and schedules a retry with exponential backoff; after 8 attempts it is dead.
"""

from __future__ import annotations

import logging
import random
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from suwapath.contracts.errors import SuwaPathError
from suwapath.contracts.events import EventEnvelope
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.models import InboxRow, OutboxRow
from suwapath.platform.uow import UnitOfWorkFactory

logger = logging.getLogger("suwapath.events")
MAX_ATTEMPTS = 8


def backoff(attempts: int) -> timedelta:
    """1 s, 2 s, 4 s ... with up to 20% jitter (EVT-7)."""
    base = 2 ** max(attempts - 1, 0)
    return timedelta(seconds=base * (1 + random.uniform(0, 0.2)))  # noqa: S311 (not security)


class Dispatcher:
    def __init__(self, uow_factory: UnitOfWorkFactory, subscribers: SubscriberRegistry) -> None:
        self.uow_factory = uow_factory
        self.subscribers = subscribers

    async def fan_out(self, batch: int = 100) -> int:
        async with self.uow_factory() as uow:
            now = uow.clock.now()
            rows = (
                await uow.session.execute(
                    select(OutboxRow.event_id, OutboxRow.type, OutboxRow.version)
                    .where(OutboxRow.status == "pending", OutboxRow.available_at <= now)
                    .order_by(OutboxRow.event_id)
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for event_id, event_type, version in rows:
                for subscriber in self.subscribers.for_event(event_type, version):
                    await uow.session.execute(
                        insert(InboxRow)
                        .values(event_id=event_id, subscriber=subscriber.name, available_at=now)
                        .on_conflict_do_nothing(index_elements=["event_id", "subscriber"])
                    )
            if rows:
                await uow.session.execute(
                    update(OutboxRow)
                    .where(OutboxRow.event_id.in_([r.event_id for r in rows]))
                    .values(status="fanned_out")
                )
            await uow.commit()
            return len(rows)

    async def deliver_one(self) -> bool:
        """Deliver one due event to one subscriber. Returns False when nothing is due."""
        failed: tuple[int, int, str] | None = None
        async with self.uow_factory() as uow:
            now = uow.clock.now()
            claimed = (
                await uow.session.execute(
                    select(InboxRow, OutboxRow)
                    .join(OutboxRow, OutboxRow.event_id == InboxRow.event_id)
                    .where(InboxRow.status == "pending", InboxRow.available_at <= now)
                    .order_by(InboxRow.available_at, InboxRow.id)
                    .limit(1)
                    .with_for_update(of=InboxRow, skip_locked=True)
                )
            ).first()
            if claimed is None:
                return False
            inbox, outbox = claimed
            subscriber = self.subscribers.get(inbox.subscriber)
            envelope = EventEnvelope.from_record(_record(outbox))
            try:
                await subscriber.handler(envelope, uow)
                inbox.status = "done"
                inbox.attempts += 1
                inbox.updated_at = now
                await uow.commit()
                return True
            except Exception as exc:
                code = exc.code if isinstance(exc, SuwaPathError) else type(exc).__name__
                failed = (inbox.id, inbox.attempts + 1, code)
                logger.warning(
                    "subscriber failed",
                    extra={
                        "subscriber": inbox.subscriber,
                        "event_type": outbox.type,
                        "attempt": inbox.attempts + 1,
                        "error_code": code,
                    },
                )
        if failed is not None:
            await self._record_failure(*failed)
        return True

    async def _record_failure(self, inbox_id: int, attempts: int, code: str) -> None:
        async with self.uow_factory() as uow:
            now = uow.clock.now()
            dead = attempts >= MAX_ATTEMPTS
            await uow.session.execute(
                update(InboxRow)
                .where(InboxRow.id == inbox_id)
                .values(
                    attempts=attempts,
                    status="dead" if dead else "pending",
                    available_at=now + backoff(attempts),
                    last_error_code=code[:80],
                    updated_at=now,
                )
            )
            await uow.commit()

    async def drain(self, max_deliveries: int = 1000) -> int:
        """Fan out and deliver until nothing is due (worker loop and tests)."""
        delivered = 0
        while delivered < max_deliveries:
            await self.fan_out()
            if not await self.deliver_one():
                break
            delivered += 1
        return delivered

    async def replay(self, inbox_id: int) -> None:
        """Admin replay of a dead delivery (EVT-7)."""
        async with self.uow_factory() as uow:
            await uow.session.execute(
                update(InboxRow)
                .where(InboxRow.id == inbox_id, InboxRow.status == "dead")
                .values(status="pending", attempts=0, available_at=uow.clock.now())
            )
            await uow.commit()


def _record(row: OutboxRow) -> dict[str, object]:
    return {
        "event_id": row.event_id,
        "type": row.type,
        "version": row.version,
        "occurred_at": row.occurred_at,
        "producer": row.producer,
        "aggregate_id": row.aggregate_id,
        "patient_id": row.patient_id,
        "correlation_id": row.correlation_id,
        "causation_id": row.causation_id,
        "trace_id": row.trace_id,
        "payload": row.payload,
    }
