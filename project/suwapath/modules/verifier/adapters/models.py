"""verifier tables (VER-5, VER-6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Float, Index, Integer, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, UuidKey
from suwapath.platform.db import Base


class VerdictRow(UuidKey, Base):
    __tablename__ = "verifier_verdicts"
    message_id: Mapped[UUID] = mapped_column(index=True)
    case_id: Mapped[UUID | None] = mapped_column()
    verdict: Mapped[str] = mapped_column(String(10))
    decided_by: Mapped[str] = mapped_column(String(8))
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    model_probabilities: Mapped[dict[str, float]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    model_checks: Mapped[str] = mapped_column(String(8))
    model_id: Mapped[str | None] = mapped_column(String(80))
    cost_usd: Mapped[float] = mapped_column(Float, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, server_default="0")
    at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))


class ReviewRow(UuidKey, Base):
    __tablename__ = "verifier_reviews"
    message_id: Mapped[UUID] = mapped_column(index=True)
    reviewer_id: Mapped[UUID] = mapped_column(index=True)
    reviewer_role: Mapped[str] = mapped_column(String(10))
    case_id: Mapped[UUID] = mapped_column()
    patient_id: Mapped[UUID] = mapped_column()
    # What the reviewer sees (intent, recipient, body), sealed with the patient's key (SEC-7).
    preview_enc: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(10), server_default="pending")
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String(40)), server_default="{}")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    decided_at: Mapped[datetime | None] = mapped_column(TZ)
    __table_args__ = (
        Index(
            "ix_verifier_reviews_pending",
            "reviewer_id",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )
