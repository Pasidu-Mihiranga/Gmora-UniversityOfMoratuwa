"""identity tables (SEC-1 to SEC-6)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, Timestamps, UuidKey
from suwapath.platform.db import Base


class UserRow(UuidKey, Timestamps, Base):
    __tablename__ = "identity_users"
    role: Mapped[str] = mapped_column(String(20))
    login: Mapped[str] = mapped_column(String(160), unique=True)  # email or phone, normalized
    display_name: Mapped[str] = mapped_column(String(100))
    language: Mapped[str] = mapped_column(String(2))
    password_hash: Mapped[str] = mapped_column(String(200))
    patient_id: Mapped[UUID | None]
    practitioner_id: Mapped[UUID | None]
    organization_id: Mapped[UUID | None]
    status: Mapped[str] = mapped_column(String(16), server_default="active")


class RefreshTokenRow(UuidKey, Base):
    __tablename__ = "identity_refresh_tokens"
    user_id: Mapped[UUID] = mapped_column(index=True)
    family_id: Mapped[UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(TZ)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))


class LoginAttemptRow(Base):
    __tablename__ = "identity_login_attempts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    login_hash: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    __table_args__ = (Index("ix_identity_login_attempts_login_at", "login_hash", "at"),)
