"""Column helpers shared by module table definitions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, LargeBinary, text
from sqlalchemy.orm import Mapped, mapped_column

TZ = DateTime(timezone=True)
# Encrypted fields are stored as bytea; the EncryptedString/EncryptedJSON types (M04) wrap them.
Encrypted = LargeBinary


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))


class UuidKey:
    id: Mapped[UUID] = mapped_column(primary_key=True)
