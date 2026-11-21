"""memory_items (MEM-2): each item has an owner, source, purpose, consent category and retention."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, Integer, String, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, Encrypted, UuidKey
from suwapath.platform.db import Base


class MemoryItemRow(UuidKey, Base):
    __tablename__ = "memory_items"
    owner_id: Mapped[UUID] = mapped_column(index=True)
    subject_patient_id: Mapped[UUID | None]
    kind: Mapped[str] = mapped_column(String(24))
    category: Mapped[str] = mapped_column(String(40))
    purpose: Mapped[str] = mapped_column(String(40))
    content_enc: Mapped[bytes] = mapped_column(Encrypted)
    source_refs: Mapped[list[str]] = mapped_column(ARRAY(String(80)), server_default="{}")
    status: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    confirmed_at: Mapped[datetime | None] = mapped_column(TZ)
    expires_at: Mapped[datetime | None] = mapped_column(TZ)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    __table_args__ = (
        Index("ix_memory_items_subject_kind", "subject_patient_id", "kind", "created_at"),
    )
