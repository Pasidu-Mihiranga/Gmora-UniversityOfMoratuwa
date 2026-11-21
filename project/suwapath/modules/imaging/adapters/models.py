"""imaging tables (IMG-1 to IMG-8)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, Float, String, text
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, UuidKey
from suwapath.platform.db import Base


class StudyRow(UuidKey, Base):
    __tablename__ = "imaging_studies"
    patient_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))


class ResultRow(UuidKey, Base):
    __tablename__ = "imaging_results"
    study_id: Mapped[UUID] = mapped_column(index=True)
    model_version: Mapped[str | None] = mapped_column(String(60))
    validation_status: Mapped[str | None] = mapped_column(String(12))
    band: Mapped[str] = mapped_column(String(24))
    probability: Mapped[float | None] = mapped_column(Float)
    flagged: Mapped[bool] = mapped_column(Boolean)
    threshold_used: Mapped[float | None] = mapped_column(Float)
    gate: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
