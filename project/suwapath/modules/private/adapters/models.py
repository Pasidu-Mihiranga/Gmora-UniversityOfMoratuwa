"""private consult tables: ciphertext only, never plaintext (PRV-1 to PRV-14)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, LargeBinary, String, text
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, UuidKey
from suwapath.platform.db import Base


class PrivateSessionRow(UuidKey, Base):
    __tablename__ = "private_sessions"
    user_id: Mapped[UUID] = mapped_column(index=True)
    retention: Mapped[str] = mapped_column(String(4))
    started_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    expires_at: Mapped[datetime | None] = mapped_column(TZ)
    deleted_at: Mapped[datetime | None] = mapped_column(TZ)


class PrivateMessageRow(UuidKey, Base):
    __tablename__ = "private_messages"
    session_id: Mapped[UUID]
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)  # encrypted in the browser (PRV-8)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column(TZ)
    __table_args__ = (
        Index("ix_private_messages_session", "session_id", "created_at"),
        Index("ix_private_messages_expiry", "expires_at"),
    )
