"""The worker wakes on NOTIFY, delivers events and fires timers after a clock advance."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from suwapath.contracts.enums import DoseSlot
from suwapath.contracts.events import EventEnvelope
from suwapath.contracts.events.catalog import DoseMissed
from suwapath.contracts.ids import PatientId, ResourceId
from suwapath.platform.clock_store import SharedClock
from suwapath.platform.db import Database
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.events.worker import Worker
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

pytestmark = pytest.mark.integration


async def test_worker_delivers_on_notify_and_fires_timers_after_clock_advance(
    db: Database, database_url: str
) -> None:
    seen = {"care.dose_missed": asyncio.Event(), "platform.timer_fired": asyncio.Event()}
    registry = SubscriberRegistry()

    async def on_event(envelope: EventEnvelope, uow: UnitOfWork) -> None:
        seen[envelope.type].set()

    registry.add("care.dose_missed", on_event, name="test.dose_missed")
    registry.add("platform.timer_fired", on_event, name="test.timer_fired")
    clock = SharedClock()
    factory = UnitOfWorkFactory(db, clock)
    worker = Worker(db, factory, registry, database_url, clock)
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.sleep(0.3)
        async with factory() as uow:
            uow.publish(
                DoseMissed(
                    administration_id=ResourceId(uuid4()),
                    statement_id=ResourceId(uuid4()),
                    slot=DoseSlot.EVENING,
                    scheduled_at=datetime(2026, 11, 27, 13, 30, tzinfo=UTC),
                    patient_id=PatientId(uuid4()),
                ),
                aggregate_id="a",
            )
            await uow.schedule_timer(
                kind="approval_timeout",
                due_at=clock.now() + timedelta(hours=4),
                ref_type="case",
                ref_id="c1",
                dedupe_key="approval:c1",
            )
            await uow.commit()
        await asyncio.wait_for(seen["care.dose_missed"].wait(), timeout=4)
        assert not seen["platform.timer_fired"].is_set()

        async with db.sessions() as session, session.begin():
            await SharedClock().advance(session, timedelta(hours=4, minutes=1))
        await asyncio.wait_for(seen["platform.timer_fired"].wait(), timeout=4)
    finally:
        worker.stop()
        await asyncio.wait_for(task, timeout=5)
