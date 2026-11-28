"""Server-sent events (API-20, API-21).

Writers call `notify_stream(uow, key)` inside their transaction; Postgres delivers the NOTIFY only
when that transaction commits, so a stream never shows uncommitted data. Each API process keeps
one `LISTEN suwapath_stream` connection (`StreamHub`) and wakes the open streams of that key, so
any replica can serve any stream. A woken stream re-reads what it may see (SEC-12).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from typing import Any

import psycopg
from sqlalchemy import text

from suwapath.contracts.errors import QuotaExceeded
from suwapath.contracts.ids import UserId
from suwapath.platform.uow import UnitOfWork

logger = logging.getLogger("suwapath.streams")
STREAM_CHANNEL = "suwapath_stream"
HEARTBEAT_SECONDS = 15.0  # API-20
MAX_STREAMS_PER_USER = 5  # spec 20 limits


async def notify_stream(uow: UnitOfWork, key: str) -> None:
    """Wake the streams of `key` once this unit of work commits. The key carries no PHI."""
    await uow.session.execute(
        text("SELECT pg_notify(:channel, :key)"), {"channel": STREAM_CHANNEL, "key": key}
    )


def sse(event: str, data: Any, event_id: str | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'), default=str)}")
    return "\n".join(lines) + "\n\n"


HEARTBEAT = ": heartbeat\n\n"


class Subscription:
    def __init__(self, hub: StreamHub, key: str, user_id: UserId) -> None:
        self.hub, self.key, self.user_id = hub, key, user_id
        self.wake = asyncio.Event()

    async def wait(self) -> bool:
        """True when woken by a write, False when it is time for a heartbeat."""
        try:
            async with asyncio.timeout(self.hub.heartbeat_seconds):
                await self.wake.wait()
        except TimeoutError:
            return False
        self.wake.clear()
        return True

    def close(self) -> None:
        self.hub._remove(self)


class StreamHub:
    def __init__(self, database_url: str, heartbeat_seconds: float = HEARTBEAT_SECONDS) -> None:
        self.heartbeat_seconds = heartbeat_seconds
        self._url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self._subs: dict[str, set[Subscription]] = defaultdict(set)
        self._per_user: dict[UserId, int] = defaultdict(int)
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    async def subscribe(self, key: str, user_id: UserId) -> Subscription:
        if self._per_user[user_id] >= MAX_STREAMS_PER_USER:
            raise QuotaExceeded("too many open streams")
        if self._task is None or self._task.done():
            self._ready.clear()
            self._task = asyncio.create_task(self._listen())
        await asyncio.wait_for(self._ready.wait(), timeout=5)
        sub = Subscription(self, key, user_id)
        self._subs[key].add(sub)
        self._per_user[user_id] += 1
        return sub

    def _remove(self, sub: Subscription) -> None:
        if sub in self._subs.get(sub.key, set()):
            self._subs[sub.key].discard(sub)
            self._per_user[sub.user_id] -= 1

    def publish_local(self, key: str) -> None:
        for sub in list(self._subs.get(key, ())):
            sub.wake.set()

    async def _listen(self) -> None:
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._url, autocommit=True
                ) as conn:
                    await conn.execute(f"LISTEN {STREAM_CHANNEL}")
                    self._ready.set()
                    async for note in conn.notifies():
                        self.publish_local(note.payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # reconnect; open streams keep their heartbeat meanwhile
                logger.warning("stream listener lost", extra={"error_code": type(exc).__name__})
                # Wake everyone so streams re-read after the gap instead of missing writes.
                for key in list(self._subs):
                    self.publish_local(key)
                await asyncio.sleep(1)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
