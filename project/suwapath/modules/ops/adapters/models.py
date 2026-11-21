"""ops tables: model call ledger, budgets, per-person usage (GW-10, CUS-9b)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, Date, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ
from suwapath.platform.db import Base


class ModelCallRow(Base):
    __tablename__ = "ops_model_calls"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(TZ)
    case_id: Mapped[UUID | None] = mapped_column(index=True)
    agent_kind: Mapped[str | None] = mapped_column(String(40))
    purpose: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 8))
    latency_ms: Mapped[int] = mapped_column(Integer)
    ttft_ms: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(8))
    simulated: Mapped[bool] = mapped_column(Boolean)
    tier: Mapped[str] = mapped_column(String(16))
    trace_id: Mapped[str] = mapped_column(String(32))
    __table_args__ = (Index("ix_ops_model_calls_at_brin", "at", postgresql_using="brin"),)


class BudgetRow(Base):
    __tablename__ = "ops_budgets"
    scope: Mapped[str] = mapped_column(String(20), primary_key=True)
    limit_usd: Mapped[float] = mapped_column(Numeric(10, 4))
    limit_calls: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    updated_by: Mapped[str | None] = mapped_column(String(80))


class UsageRow(Base):
    """Per-person daily quota counters, reserved atomically (CUS-9b)."""

    __tablename__ = "ops_usage"
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    turns: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), server_default="0")
