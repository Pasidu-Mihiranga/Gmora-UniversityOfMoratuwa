"""ops: budgets, quotas, model call ledger, AI operations metrics (GW-10, CUS-8 to CUS-9b)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import AgentKind, Purpose
from suwapath.contracts.ids import CaseId, TraceId, UserId


class UsageReservation(Contract):
    id: str
    user_id: UserId | None
    case_id: CaseId | None
    estimated_cost_usd: float


class ModelCallRecord(Contract):
    at: datetime
    case_id: CaseId | None
    agent_kind: AgentKind | None
    purpose: Purpose
    provider: str
    model: str
    prompt_version: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    ttft_ms: int | None
    outcome: Literal["ok", "error", "timeout", "blocked"]
    simulated: bool
    tier: str
    trace_id: TraceId


class OpsOverview(Contract):
    provider_mode: Literal["real", "simulated"]
    decision_provider: Literal["jev", "none"]
    cost_today_usd: float
    calls_today: int
    p95_latency_ms: int | None
    outbox_lag_seconds: float
    queued_agent_jobs: int
    dead_letters: int


class OpsService(Protocol):
    async def reserve(
        self,
        user_id: UserId | None,
        case_id: CaseId | None,
        estimated_cost_usd: float,
        care_access: bool,
    ) -> UsageReservation:
        """Atomic quota and budget reservation (CUS-9b). care_access=True never raises (CUS-9a)."""
        ...

    async def reconcile(self, reservation: UsageReservation, record: ModelCallRecord) -> None: ...

    async def overview(self) -> OpsOverview: ...
