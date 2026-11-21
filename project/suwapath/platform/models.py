"""Tables owned by the platform (11-persistence.md section 3): delivery, timers, clock, flags,
idempotency keys and the agent job queue."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.db import Base

TZ = DateTime(timezone=True)


class OutboxRow(Base):
    """One integration event (EVT-1 to EVT-5). Written in the producer's transaction."""

    __tablename__ = "platform_outbox"

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(SmallInteger)
    occurred_at: Mapped[datetime] = mapped_column(TZ)
    producer: Mapped[str] = mapped_column(String(40))
    aggregate_id: Mapped[str] = mapped_column(String(80))
    patient_id: Mapped[UUID | None]
    correlation_id: Mapped[UUID | None]
    causation_id: Mapped[UUID | None]
    trace_id: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")  # pending, fanned_out
    available_at: Mapped[datetime] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))

    __table_args__ = (
        Index(
            "ix_platform_outbox_pending",
            "available_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )


class InboxRow(Base):
    """Delivery of one event to one subscriber; doubles as the idempotency record (EVT-6, EVT-7)."""

    __tablename__ = "platform_inbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID]
    subscriber: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), server_default="pending")  # pending, done, dead
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    available_at: Mapped[datetime] = mapped_column(TZ)
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("event_id", "subscriber"),
        Index("ix_platform_inbox_due", "available_at", postgresql_where=text("status = 'pending'")),
    )


class TimerRow(Base):
    """Durable timer (EVT-14, EVT-15)."""

    __tablename__ = "platform_timers"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(60))
    due_at: Mapped[datetime] = mapped_column(TZ)
    ref_type: Mapped[str] = mapped_column(String(60))
    ref_id: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(
        String(16), server_default="scheduled"
    )  # scheduled, fired, cancelled
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    fired_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))

    __table_args__ = (
        Index("ix_platform_timers_due", "due_at", postgresql_where=text("status = 'scheduled'")),
    )


class ClockRow(Base):
    """Shared offset of the simulated clock (EVT-17). Exactly one row, id 1."""

    __tablename__ = "platform_clock"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    offset_seconds: Mapped[int] = mapped_column(BigInteger, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))


class FlagRow(Base):
    __tablename__ = "platform_flags"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    description: Mapped[str] = mapped_column(Text, server_default="")
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    updated_by: Mapped[str | None] = mapped_column(String(80))


class IdempotencyKeyRow(Base):
    """API-6: same key and body within 24 h returns the stored response."""

    __tablename__ = "platform_idempotency_keys"

    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    route: Mapped[str] = mapped_column(String(120), primary_key=True)
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(SmallInteger)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))


class AgentJobRow(Base):
    """Agent work queue (EVT-10 to EVT-12)."""

    __tablename__ = "agents_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    agent_kind: Mapped[str] = mapped_column(String(40))
    case_id: Mapped[UUID | None]
    patient_id: Mapped[UUID | None]
    trigger_event_id: Mapped[UUID | None]
    acting_for: Mapped[UUID | None]
    purpose: Mapped[str] = mapped_column(String(40))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(
        String(16), server_default="queued"
    )  # queued running waiting done failed
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    available_at: Mapped[datetime] = mapped_column(TZ)
    locked_by: Mapped[str | None] = mapped_column(String(80))
    lease_until: Mapped[datetime | None] = mapped_column(TZ)
    error_code: Mapped[str | None] = mapped_column(String(80))
    trace_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))

    __table_args__ = (
        Index("ix_agents_jobs_queued", "available_at", postgresql_where=text("status = 'queued'")),
        Index(
            "uq_agents_jobs_one_running",
            "case_id",
            "agent_kind",
            unique=True,
            postgresql_where=text("status = 'running' AND case_id IS NOT NULL"),
        ),
    )
