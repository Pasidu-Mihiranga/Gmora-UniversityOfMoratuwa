"""agents_messages (AGT-10 to AGT-13). The job queue table agents_jobs is owned by platform.jobs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, Encrypted, Timestamps, UuidKey
from suwapath.platform.db import Base


class AgentMessageRow(UuidKey, Timestamps, Base):
    __tablename__ = "agents_messages"
    conversation_id: Mapped[UUID]
    in_reply_to: Mapped[UUID | None]
    case_id: Mapped[UUID]
    intent: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))
    sender: Mapped[str] = mapped_column(String(40))
    recipient: Mapped[str] = mapped_column(String(40))
    acting_for: Mapped[UUID | None]
    consent_scope: Mapped[list[str]] = mapped_column(ARRAY(String(40)), server_default="{}")
    body_enc: Mapped[bytes] = mapped_column(Encrypted)
    status: Mapped[str] = mapped_column(String(12))
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)  # retries never send twice
    hop: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, server_default="0")
    deadline: Mapped[datetime | None] = mapped_column(TZ)
    trace_id: Mapped[str] = mapped_column(String(32))
    simulated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    __table_args__ = (Index("ix_agents_messages_case", "case_id", "created_at"),)
