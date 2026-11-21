"""audit_log: append-only, hash-chained (AUD-1 to AUD-6). No PHI in any column."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, UuidKey
from suwapath.platform.db import Base


class AuditRow(UuidKey, Base):
    __tablename__ = "audit_log"
    at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    actor_kind: Mapped[str] = mapped_column(String(10))
    actor_id: Mapped[str | None] = mapped_column(String(80))
    agent_kind: Mapped[str | None] = mapped_column(String(40))
    acting_for: Mapped[UUID | None]
    patient_id: Mapped[UUID | None]
    action: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(String(80))
    purpose: Mapped[str | None] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str | None] = mapped_column(String(80))
    trace_id: Mapped[str] = mapped_column(String(32))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        Index("ix_audit_log_patient_at", "patient_id", "at"),
        Index("ix_audit_log_actor_at", "actor_id", "at"),
        Index("ix_audit_log_trace", "trace_id"),
        Index("ix_audit_log_at_brin", "at", postgresql_using="brin"),
    )
