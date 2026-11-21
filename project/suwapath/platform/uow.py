"""Unit of work (DB-5, EVT-1): one transaction for state, case events, outbox, timers and audit.

    async with uow_factory() as uow:
        ...change state with uow.session...
        uow.publish(DoseMissed(...), aggregate_id=..., patient_id=...)
        uow.schedule_timer(...)
        await uow.commit()

Leaving the block without `commit()` rolls everything back.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from types import TracebackType
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from suwapath.contracts.events import EventEnvelope, EventPayload
from suwapath.contracts.ids import CaseId, EventId, PatientId, TraceId
from suwapath.contracts.ports.audit import AuditEntryDraft
from suwapath.platform.clock import Clock
from suwapath.platform.db import Database
from suwapath.platform.ids import new_id
from suwapath.platform.models import OutboxRow, TimerRow
from suwapath.platform.tracing import current_trace_id

AuditWriter = Callable[[AsyncSession, AuditEntryDraft], Awaitable[None]]
OUTBOX_CHANNEL = "suwapath_outbox"


class UnitOfWork:
    def __init__(self, db: Database, clock: Clock, audit_writer: AuditWriter | None = None) -> None:
        self._db = db
        self.clock = clock
        self._audit_writer = audit_writer
        self._session: AsyncSession | None = None
        self._published: list[EventEnvelope] = []
        self.unwritten_audit: list[AuditEntryDraft] = []  # used only when no writer is bound
        self._committed = False

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside 'async with'")
        return self._session

    @property
    def published(self) -> list[EventEnvelope]:
        return list(self._published)

    async def __aenter__(self) -> UnitOfWork:
        self._session = self._db.sessions()
        await self._session.begin()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self.session
        try:
            if not self._committed:
                await session.rollback()
        finally:
            await session.close()
            self._session = None

    def publish(
        self,
        payload: EventPayload,
        *,
        aggregate_id: str,
        patient_id: PatientId | None = None,
        correlation_id: CaseId | None = None,
        causation_id: EventId | None = None,
        available_at: datetime | None = None,
    ) -> EventEnvelope:
        now = self.clock.now()
        envelope = EventEnvelope.wrap(
            payload,
            event_id=EventId(new_id()),
            occurred_at=now,
            aggregate_id=aggregate_id,
            trace_id=TraceId(current_trace_id()),
            patient_id=patient_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        record = envelope.to_record()
        self.session.add(
            OutboxRow(
                event_id=envelope.event_id,
                type=envelope.type,
                version=envelope.version,
                occurred_at=now,
                producer=envelope.producer,
                aggregate_id=aggregate_id,
                patient_id=patient_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
                trace_id=envelope.trace_id,
                payload=record["payload"],
                available_at=available_at or now,
            )
        )
        self._published.append(envelope)
        return envelope

    async def schedule_timer(
        self,
        *,
        kind: str,
        due_at: datetime,
        ref_type: str,
        ref_id: str,
        dedupe_key: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """Idempotent by dedupe_key. Returns False when the timer already existed."""
        result = await self.session.execute(
            insert(TimerRow)
            .values(
                id=new_id(),
                kind=kind,
                due_at=due_at,
                ref_type=ref_type,
                ref_id=ref_id,
                dedupe_key=dedupe_key,
                payload=payload or {},
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
            .returning(TimerRow.id)
        )
        return result.scalar_one_or_none() is not None

    async def cancel_timer(self, dedupe_key: str) -> bool:
        result = await self.session.execute(
            update(TimerRow)
            .where(TimerRow.dedupe_key == dedupe_key, TimerRow.status == "scheduled")
            .values(status="cancelled")
            .returning(TimerRow.id)
        )
        return result.scalar_one_or_none() is not None

    async def audit(self, entry: AuditEntryDraft) -> None:
        """AUD-5: written in this transaction by the audit module's writer once it is bound."""
        if self._audit_writer is None:
            self.unwritten_audit.append(entry)
            return
        await self._audit_writer(self.session, entry)

    async def commit(self) -> None:
        if self._published:
            await self.session.execute(text(f"NOTIFY {OUTBOX_CHANNEL}"))
        await self.session.commit()
        self._committed = True


class UnitOfWorkFactory:
    def __init__(self, db: Database, clock: Clock, audit_writer: AuditWriter | None = None) -> None:
        self.db = db
        self.clock = clock
        self.audit_writer = audit_writer

    def __call__(self) -> UnitOfWork:
        return UnitOfWork(self.db, self.clock, self.audit_writer)
