"""booking tables: directory, sessions, slots, holds, appointments (BOOK-1 to BOOK-7)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, Float, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, Encrypted, Timestamps, UuidKey
from suwapath.platform.db import Base


class OrganizationRow(UuidKey, Timestamps, Base):
    __tablename__ = "booking_organizations"
    name: Mapped[str] = mapped_column(String(120))
    district: Mapped[str] = mapped_column(String(40))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    phone: Mapped[str | None] = mapped_column(String(20))
    __table_args__ = (Index("ix_booking_organizations_lat_lng", "lat", "lng"),)


class PractitionerRoleRow(UuidKey, Base):
    __tablename__ = "booking_practitioner_roles"
    practitioner_id: Mapped[UUID] = mapped_column(index=True)
    organization_id: Mapped[UUID] = mapped_column(index=True)
    specialty: Mapped[str] = mapped_column(String(40), index=True)
    fee_lkr: Mapped[int] = mapped_column(Integer)


class ScheduleRow(UuidKey, Base):
    __tablename__ = "booking_schedules"
    practitioner_role_id: Mapped[UUID] = mapped_column(index=True)
    organization_id: Mapped[UUID] = mapped_column(index=True)
    starts_at: Mapped[datetime] = mapped_column(TZ)
    ends_at: Mapped[datetime] = mapped_column(TZ)
    capacity: Mapped[int] = mapped_column(Integer)


class SlotRow(UuidKey, Base):
    __tablename__ = "booking_slots"
    schedule_id: Mapped[UUID] = mapped_column(index=True)
    practitioner_role_id: Mapped[UUID]
    number: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(TZ)
    status: Mapped[str] = mapped_column(String(8), server_default="free")
    __table_args__ = (
        Index("ix_booking_slots_role_start", "practitioner_role_id", "starts_at"),
        Index("ix_booking_slots_free", "starts_at", postgresql_where=text("status = 'free'")),
    )


class HoldRow(UuidKey, Base):
    __tablename__ = "booking_holds"
    slot_id: Mapped[UUID] = mapped_column(
        unique=True
    )  # one live hold per slot; expired holds are deleted
    patient_id: Mapped[UUID]
    case_id: Mapped[UUID | None]
    sensitive: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    expires_at: Mapped[datetime] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))


class AppointmentRow(UuidKey, Timestamps, Base):
    __tablename__ = "booking_appointments"
    patient_id: Mapped[UUID]
    slot_id: Mapped[UUID]
    practitioner_role_id: Mapped[UUID]
    organization_id: Mapped[UUID]
    starts_at: Mapped[datetime] = mapped_column(TZ)
    status: Mapped[str] = mapped_column(String(12))
    case_id: Mapped[UUID | None]
    sensitive: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # BOOK-7: the hospital sees the first name only; sealed with the patient's key.
    first_name_enc: Mapped[bytes | None] = mapped_column(Encrypted)
    __table_args__ = (
        Index(
            "uq_booking_appointments_one_booked_per_slot",
            "slot_id",
            unique=True,
            postgresql_where=text("status = 'booked'"),
        ),
        Index("ix_booking_appointments_patient_start", "patient_id", "starts_at"),
    )


class StoppedCaseRow(Base):
    """BOOK-5, SAF-10: a case with a red flag; no hold or booking may be made for it."""

    __tablename__ = "booking_stopped_cases"
    case_id: Mapped[UUID] = mapped_column(primary_key=True)
    stopped_at: Mapped[datetime] = mapped_column(TZ)
