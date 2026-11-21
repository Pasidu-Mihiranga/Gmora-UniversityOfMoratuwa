"""consent tables: grants, guardian links, rules and customisation (SEC-10 to SEC-15, CUS)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, Encrypted, Timestamps, UuidKey
from suwapath.platform.db import Base


class GrantRow(UuidKey, Timestamps, Base):
    __tablename__ = "consent_grants"
    patient_id: Mapped[UUID]
    grantee_id: Mapped[UUID]
    grantee_role: Mapped[str] = mapped_column(String(20))
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(40)))
    purposes: Mapped[list[str]] = mapped_column(ARRAY(String(40)))
    status: Mapped[str] = mapped_column(String(16))
    period_start: Mapped[datetime] = mapped_column(TZ)
    period_end: Mapped[datetime | None] = mapped_column(TZ)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    __table_args__ = (
        Index(
            "ix_consent_grants_active",
            "patient_id",
            "grantee_id",
            postgresql_where=text("status = 'active'"),
        ),
    )


class RelatedPersonRow(UuidKey, Timestamps, Base):
    __tablename__ = "consent_related_persons"
    patient_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    relationship: Mapped[str] = mapped_column(String(20))
    lives_abroad: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    data_enc: Mapped[bytes] = mapped_column(Encrypted)


class RuleRow(UuidKey, Timestamps, Base):
    __tablename__ = "consent_rules"
    owner_id: Mapped[UUID] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(40))
    active_version: Mapped[int] = mapped_column(Integer)


class RuleVersionRow(UuidKey, Base):
    __tablename__ = "consent_rule_versions"
    rule_id: Mapped[UUID] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))


class SettingRow(Base):
    __tablename__ = "customisation_settings"
    owner_id: Mapped[UUID] = mapped_column(primary_key=True)
    setting_key: Mapped[str] = mapped_column(String(60), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    value: Mapped[Any] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))


class SettingVersionRow(UuidKey, Base):
    __tablename__ = "customisation_versions"
    owner_id: Mapped[UUID]
    setting_key: Mapped[str] = mapped_column(String(60))
    version: Mapped[int] = mapped_column(Integer)
    value: Mapped[Any] = mapped_column(JSONB)
    changed_by: Mapped[UUID]
    changed_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    __table_args__ = (
        Index("ix_customisation_versions_owner_key", "owner_id", "setting_key", "version"),
    )


class SuggestionRow(UuidKey, Base):
    __tablename__ = "customisation_suggestions"
    patient_id: Mapped[UUID] = mapped_column(index=True)
    guardian_id: Mapped[UUID]
    setting_key: Mapped[str] = mapped_column(String(60))
    proposed: Mapped[Any] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    decided_at: Mapped[datetime | None] = mapped_column(TZ)
