"""care tables: programmes, offerings, referrals, enrolment, dose events, alert rules."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, Encrypted, Timestamps, UuidKey
from suwapath.platform.db import Base


class ProgrammeTypeRow(Base):
    __tablename__ = "care_programme_types"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    content_version: Mapped[str] = mapped_column(String(20))


class OfferingRow(UuidKey, Timestamps, Base):
    __tablename__ = "care_offerings"
    programme_key: Mapped[str] = mapped_column(String(40))
    organization_id: Mapped[UUID] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, server_default="")
    schedule_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    practitioner_role_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    capacity: Mapped[int] = mapped_column(Integer)
    fee_lkr: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), server_default="draft")
    __table_args__ = (Index("ix_care_offerings_programme_status", "programme_key", "status"),)


class ReferralRow(UuidKey, Base):
    __tablename__ = "care_referrals"
    patient_id: Mapped[UUID] = mapped_column(index=True)
    programme_key: Mapped[str] = mapped_column(String(40))
    offering_id: Mapped[UUID | None]
    referred_by: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(12), server_default="pending")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))


class EnrollmentRow(UuidKey, Base):
    __tablename__ = "care_enrollments"
    patient_id: Mapped[UUID] = mapped_column(index=True)
    programme_key: Mapped[str] = mapped_column(String(40))
    offering_id: Mapped[UUID | None]
    source: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10))
    started_at: Mapped[datetime] = mapped_column(TZ)
    ended_at: Mapped[datetime | None] = mapped_column(TZ)


class MedicationAdministrationRow(UuidKey, Base):
    __tablename__ = "care_medication_administrations"
    patient_id: Mapped[UUID]
    medication_statement_id: Mapped[UUID]
    scheduled_at: Mapped[datetime] = mapped_column(TZ)
    slot: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10))
    status_reason: Mapped[str | None] = mapped_column(String(12))
    recorded_at: Mapped[datetime] = mapped_column(TZ)
    recorded_by: Mapped[str] = mapped_column(String(12))
    data_enc: Mapped[bytes | None] = mapped_column(Encrypted)
    __table_args__ = (
        UniqueConstraint("medication_statement_id", "scheduled_at"),
        Index("ix_care_medication_administrations_patient", "patient_id", "scheduled_at"),
    )


class AlertRuleRow(UuidKey, Timestamps, Base):
    __tablename__ = "care_alert_rules"
    owner_id: Mapped[UUID] = mapped_column(index=True)
    patient_id: Mapped[UUID] = mapped_column(index=True)
    rule_type: Mapped[str] = mapped_column(String(30))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
