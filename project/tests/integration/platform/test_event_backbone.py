"""Critical-tier tests: outbox, delivery, timers, job queue and shared clock (EVT-1 to EVT-21)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import func, select

from suwapath.contracts.agents import AgentJob
from suwapath.contracts.enums import AgentKind, DoseSlot, Purpose
from suwapath.contracts.events import EventEnvelope
from suwapath.contracts.events.catalog import DoseMissed, TimerFired
from suwapath.contracts.ids import CaseId, JobId, PatientId, ResourceId, TraceId
from suwapath.platform.clock import FixedClock
from suwapath.platform.clock_store import SharedClock
from suwapath.platform.db import Database
from suwapath.platform.events.dispatcher import MAX_ATTEMPTS, Dispatcher
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.jobs import queue
from suwapath.platform.jobs.timers import fire_due_timers
from suwapath.platform.models import FlagRow, InboxRow, OutboxRow, TimerRow
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

pytestmark = pytest.mark.integration


def _dose_missed() -> DoseMissed:
    return DoseMissed(
        administration_id=ResourceId(uuid4()),
        statement_id=ResourceId(uuid4()),
        slot=DoseSlot.EVENING,
        scheduled_at=datetime(2026, 11, 27, 13, 30, tzinfo=UTC),
        patient_id=PatientId(uuid4()),
    )


async def _count(uow_factory: UnitOfWorkFactory, model: type, *where: object) -> int:
    async with uow_factory() as uow:
        return int(
            (
                await uow.session.execute(select(func.count()).select_from(model).where(*where))
            ).scalar_one()
        )


async def test_commit_writes_outbox_and_notifies(
    uow_factory: UnitOfWorkFactory, database_url: str
) -> None:
    listener = await psycopg.AsyncConnection.connect(
        database_url.replace("+psycopg", ""), autocommit=True
    )
    await listener.execute("LISTEN suwapath_outbox")
    async with uow_factory() as uow:
        uow.publish(_dose_missed(), aggregate_id="a1")
        await uow.commit()
    notifications = [n async for n in listener.notifies(timeout=2.0, stop_after=1)]
    await listener.close()
    assert len(notifications) == 1
    assert await _count(uow_factory, OutboxRow) == 1


async def test_no_commit_means_nothing_is_written(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        uow.publish(_dose_missed(), aggregate_id="a1")
        await uow.schedule_timer(
            kind="k", due_at=uow.clock.now(), ref_type="r", ref_id="1", dedupe_key="d"
        )
    assert await _count(uow_factory, OutboxRow) == 0
    assert await _count(uow_factory, TimerRow) == 0


async def test_each_subscriber_gets_each_event_once_with_atomic_effects(
    uow_factory: UnitOfWorkFactory,
) -> None:
    calls: list[str] = []
    registry = SubscriberRegistry()

    def handler(name: str):  # type: ignore[no-untyped-def]
        async def handle(envelope: EventEnvelope, uow: UnitOfWork) -> None:
            calls.append(name)
            uow.session.add(FlagRow(key=f"{name}:{envelope.event_id}", enabled=True))

        return handle

    registry.add("care.dose_missed", handler("guardian"), name="agents.guardian_trigger")
    registry.add("care.dose_missed", handler("ops"), name="ops.counter")
    dispatcher = Dispatcher(uow_factory, registry)
    async with uow_factory() as uow:
        uow.publish(_dose_missed(), aggregate_id="a1")
        await uow.commit()

    assert await dispatcher.drain() == 2
    assert await dispatcher.drain() == 0  # nothing redelivered
    assert sorted(calls) == ["guardian", "ops"]
    assert await _count(uow_factory, FlagRow) == 2
    assert await _count(uow_factory, InboxRow, InboxRow.status == "done") == 2


async def test_failure_rolls_back_effects_retries_then_dead_letters(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    attempts = 0
    registry = SubscriberRegistry()

    async def flaky(envelope: EventEnvelope, uow: UnitOfWork) -> None:
        nonlocal attempts
        attempts += 1
        uow.session.add(FlagRow(key=f"effect-{attempts}", enabled=True))
        raise RuntimeError("boom")

    registry.add("care.dose_missed", flaky, name="agents.flaky")
    dispatcher = Dispatcher(uow_factory, registry)
    async with uow_factory() as uow:
        uow.publish(_dose_missed(), aggregate_id="a1")
        await uow.commit()

    for _ in range(MAX_ATTEMPTS):
        await dispatcher.drain()
        clock.advance(timedelta(minutes=10))  # past any backoff

    assert attempts == MAX_ATTEMPTS
    assert await _count(uow_factory, FlagRow) == 0  # every failed effect rolled back
    assert await _count(uow_factory, InboxRow, InboxRow.status == "dead") == 1

    async with uow_factory() as uow:
        inbox_id = (await uow.session.execute(select(InboxRow.id))).scalar_one()
    await dispatcher.replay(inbox_id)
    assert await _count(uow_factory, InboxRow, InboxRow.status == "pending") == 1


async def test_retry_waits_for_backoff(uow_factory: UnitOfWorkFactory, clock: FixedClock) -> None:
    attempts = 0
    registry = SubscriberRegistry()

    async def fails_once(envelope: EventEnvelope, uow: UnitOfWork) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")

    registry.add("care.dose_missed", fails_once, name="agents.once")
    dispatcher = Dispatcher(uow_factory, registry)
    async with uow_factory() as uow:
        uow.publish(_dose_missed(), aggregate_id="a1")
        await uow.commit()

    await dispatcher.drain()
    assert attempts == 1  # not retried immediately
    clock.advance(timedelta(seconds=5))
    await dispatcher.drain()
    assert attempts == 2
    assert await _count(uow_factory, InboxRow, InboxRow.status == "done") == 1


async def test_timers_are_idempotent_cancellable_and_fire_once(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    async with uow_factory() as uow:
        now = uow.clock.now()
        assert await uow.schedule_timer(
            kind="dose_check",
            due_at=now + timedelta(hours=1),
            ref_type="dose",
            ref_id="1",
            dedupe_key="dose:1",
        )
        assert not await uow.schedule_timer(
            kind="dose_check",
            due_at=now + timedelta(hours=1),
            ref_type="dose",
            ref_id="1",
            dedupe_key="dose:1",
        )
        await uow.schedule_timer(
            kind="approval_timeout",
            due_at=now + timedelta(hours=1),
            ref_type="case",
            ref_id="c",
            dedupe_key="approval:c",
        )
        assert await uow.cancel_timer("approval:c")
        await uow.commit()

    assert await fire_due_timers(uow_factory) == 0  # not due yet
    clock.advance(timedelta(hours=2))
    assert await fire_due_timers(uow_factory) == 1  # the cancelled one never fires
    assert await fire_due_timers(uow_factory) == 0
    async with uow_factory() as uow:
        payload = (await uow.session.execute(select(OutboxRow.payload))).scalar_one()
    assert TimerFired.model_validate(payload).kind == "dose_check"


def _job(case_id: CaseId, kind: AgentKind = AgentKind.GUARDIAN_AGENT) -> AgentJob:
    return AgentJob(
        id=JobId(uuid4()),
        agent_kind=kind,
        case_id=case_id,
        patient_id=None,
        trigger_event_id=None,
        acting_for=uuid4(),
        purpose=Purpose.GUARDIAN_MONITORING,  # type: ignore[arg-type]
        trace_id=TraceId("d" * 32),
    )


async def test_one_running_job_per_case_and_agent(uow_factory: UnitOfWorkFactory) -> None:
    case_a, case_b = CaseId(uuid4()), CaseId(uuid4())
    async with uow_factory() as uow:
        for job in (_job(case_a), _job(case_a), _job(case_b)):
            queue.enqueue(uow, job)
        await uow.commit()

    first = await queue.claim(uow_factory, "w1")
    second = await queue.claim(uow_factory, "w2")
    third = await queue.claim(uow_factory, "w3")
    claimed_cases = [j.case_id for j in (first, second) if j]
    assert sorted(map(str, claimed_cases)) == sorted(map(str, [case_a, case_b]))
    assert third is None  # case_a's second job waits for the first


async def test_expired_lease_is_reclaimed(
    uow_factory: UnitOfWorkFactory, clock: FixedClock
) -> None:
    async with uow_factory() as uow:
        queue.enqueue(uow, _job(CaseId(uuid4())))
        await uow.commit()
    job = await queue.claim(uow_factory, "crashed-worker")
    assert job is not None
    assert await queue.claim(uow_factory, "w2") is None
    clock.advance(queue.LEASE + timedelta(seconds=1))
    reclaimed = await queue.claim(uow_factory, "w2")
    assert reclaimed is not None and reclaimed.id == job.id and reclaimed.attempts == 2


async def test_concurrent_claimers_never_take_the_same_job(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        for _ in range(10):
            queue.enqueue(uow, _job(CaseId(uuid4())))
        await uow.commit()

    async def worker(name: str) -> list[JobId]:
        taken: list[JobId] = []
        while (job := await queue.claim(uow_factory, name)) is not None:
            taken.append(job.id)
        return taken

    results = await asyncio.gather(*(worker(f"w{i}") for i in range(5)))
    all_ids = [job_id for batch in results for job_id in batch]
    assert len(all_ids) == 10
    assert len(set(all_ids)) == 10


async def test_shared_clock_offset_is_shared_between_processes(db: Database) -> None:
    first, second = SharedClock(), SharedClock()
    async with db.sessions() as session, session.begin():
        await first.advance(session, timedelta(hours=4))
    async with db.sessions() as session:
        await second.refresh(session)
    assert second.offset == timedelta(hours=4)
    with pytest.raises(ValueError):
        async with db.sessions() as session:
            await first.advance(session, timedelta(seconds=-1))
