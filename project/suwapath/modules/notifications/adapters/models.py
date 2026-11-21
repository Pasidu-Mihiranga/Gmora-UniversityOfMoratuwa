"""notifications_items."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, UuidKey
from suwapath.platform.db import Base


class NotificationRow(UuidKey, Base):
    __tablename__ = "notifications_items"
    user_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(40))
    template_key: Mapped[str] = mapped_column(String(80))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    urgency: Mapped[str] = mapped_column(String(10))
    link: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    read_at: Mapped[datetime | None] = mapped_column(TZ)
    __table_args__ = (
        Index(
            "ix_notifications_items_unread",
            "user_id",
            "created_at",
            postgresql_where=text("read_at IS NULL"),
        ),
    )
