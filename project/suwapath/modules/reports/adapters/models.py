"""reports tables (REP-1 to REP-4)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import String, text
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, Encrypted, UuidKey
from suwapath.platform.db import Base


class ReportRow(UuidKey, Base):
    __tablename__ = "reports_reports"
    patient_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(20))
    issued_at: Mapped[datetime | None] = mapped_column(TZ)
    data_enc: Mapped[bytes | None] = mapped_column(Encrypted)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))


class ExtractionRow(UuidKey, Base):
    __tablename__ = "reports_extractions"
    report_id: Mapped[UUID] = mapped_column(index=True)
    method: Mapped[str] = mapped_column(String(12))  # text_layer, vision, ocr
    status: Mapped[str] = mapped_column(String(20))
    data_enc: Mapped[bytes | None] = mapped_column(Encrypted)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
