"""agents_messages (AGT-10 to AGT-13). The job queue table agents_jobs is owned by platform.jobs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, Index, Integer, String, UniqueConstraint, text
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


class BriefRow(UuidKey, Base):
    """One version of a case's doctor brief (API-23). Content is the `Brief` as JSON, encrypted
    with the patient key once M04 lands. The latest version per case is the current brief."""

    __tablename__ = "agents_briefs"
    case_id: Mapped[UUID]
    patient_id: Mapped[UUID]
    built_for: Mapped[UUID]  # the doctor whose access the brief was built with
    version: Mapped[int] = mapped_column(Integer)
    content_enc: Mapped[bytes] = mapped_column(Encrypted)
    built_at: Mapped[datetime] = mapped_column(TZ)
    __table_args__ = (
        UniqueConstraint("case_id", "version"),
        Index("ix_agents_briefs_patient", "patient_id", "built_at"),
    )


class DoctorNoteRow(UuidKey, Base):
    """The treating doctor's own words for the patient (A05): the text of a reply draft, and an
    edited text when the doctor edits before approving. Never rewritten by a model."""

    __tablename__ = "agents_doctor_notes"
    case_id: Mapped[UUID] = mapped_column(index=True)
    patient_id: Mapped[UUID]
    author_id: Mapped[UUID]
    draft_id: Mapped[UUID | None]
    text_enc: Mapped[bytes] = mapped_column(Encrypted)
    created_at: Mapped[datetime] = mapped_column(TZ)
