"""The worker process loop (ARC-12, EVT-5, EVT-14).

Each pass: refresh the shared simulated clock, fire due timers, fan out and deliver events. Between
passes it waits for a Postgres NOTIFY on the outbox or clock channel, or 1 s as a fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass

import psycopg

from suwapath.platform.clock_store import CLOCK_CHANNEL, SharedClock
from suwapath.platform.db import Database
from suwapath.platform.events.dispatcher import Dispatcher
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.jobs.timers import fire_due_timers
from suwapath.platform.uow import OUTBOX_CHANNEL, UnitOfWorkFactory

logger = logging.getLogger("suwapath.worker")
POLL_SECONDS = 1.0


@dataclass
class PassResult:
    timers_fired: int
    delivered: int


class Worker:
    def __init__(
        self,
        db: Database,
        uow_factory: UnitOfWorkFactory,
        subscribers: SubscriberRegistry,
        database_url: str,
        shared_clock: SharedClock | None = None,
    ) -> None:
        self.db = db
        self.uow_factory = uow_factory
        self.dispatcher = Dispatcher(uow_factory, subscribers)
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://")
        self.shared_clock = shared_clock
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_pass(self) -> PassResult:
        if self.shared_clock is not None:
            async with self.db.sessions() as session:
                await self.shared_clock.refresh(session)
        fired = await fire_due_timers(self.uow_factory)
        delivered = await self.dispatcher.drain()
        if fired or delivered:
            logger.info("worker pass", extra={"count": delivered, "timer_kind": f"fired:{fired}"})
        return PassResult(timers_fired=fired, delivered=delivered)

    async def run(self) -> None:
        listener = await psycopg.AsyncConnection.connect(self.database_url, autocommit=True)
        try:
            await listener.execute(f"LISTEN {OUTBOX_CHANNEL}")
            await listener.execute(f"LISTEN {CLOCK_CHANNEL}")
            logger.info("worker started")
            while not self._stop.is_set():
                try:
                    await self.run_pass()
                except Exception as exc:  # a bad pass must not kill the worker
                    logger.error("worker pass failed", exc_info=exc)
                await self._wait(listener)
        finally:
            await listener.close()
            logger.info("worker stopped")

    async def _wait(self, listener: psycopg.AsyncConnection) -> None:
        stop_task = asyncio.create_task(self._stop.wait())
        notify_task = asyncio.create_task(_next_notify(listener))
        done, pending = await asyncio.wait(
            {stop_task, notify_task}, timeout=POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _next_notify(listener: psycopg.AsyncConnection) -> None:
    async for _ in listener.notifies(timeout=POLL_SECONDS, stop_after=1):
        return


def install_signal_handlers(worker: Worker) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.stop)
