"""cases tables: the aggregate, its event log, timeline projection and communications."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, Encrypted, Timestamps, UuidKey
from suwapath.platform.db import Base


class CaseRow(UuidKey, Timestamps, Base):
    __tablename__ = "cases_cases"
    patient_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(20))
    urgency: Mapped[str | None] = mapped_column(String(12))
    language: Mapped[str] = mapped_column(String(2))
    version: Mapped[int] = mapped_column(Integer)
    simulated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    closed_at: Mapped[datetime | None] = mapped_column(TZ)
    __table_args__ = (Index("ix_cases_cases_patient", "patient_id", "created_at"),)


class CaseEventRow(UuidKey, Base):
    """Append-only; (case_id, version) unique gives optimistic locking (DB-6). Data holds IDs and
    codes; patient text lives encrypted in cases_communications."""

    __tablename__ = "cases_events"
    case_id: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(TZ)
    simulated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    __table_args__ = (UniqueConstraint("case_id", "version"),)


class TimelineRow(UuidKey, Base):
    __tablename__ = "cases_timeline"
    case_id: Mapped[UUID]
    at: Mapped[datetime] = mapped_column(TZ)
    kind: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(40))
    summary_key: Mapped[str] = mapped_column(String(80))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    simulated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    __table_args__ = (Index("ix_cases_timeline_case_at", "case_id", "at"),)


class CommunicationRow(UuidKey, Base):
    __tablename__ = "cases_communications"
    case_id: Mapped[UUID] = mapped_column(index=True)
    patient_id: Mapped[UUID]
    sender: Mapped[str] = mapped_column(String(40))
    recipient: Mapped[str] = mapped_column(String(40))
    payload_enc: Mapped[bytes] = mapped_column(Encrypted)
    in_response_to: Mapped[UUID | None]
    simulated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
