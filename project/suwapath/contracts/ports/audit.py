"""audit: append-only, hash-chained audit log (AUD-1 to AUD-6). Entries never hold PHI."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import Field

from suwapath.contracts.common import Contract, Page
from suwapath.contracts.enums import AgentKind, PrincipalKind, Purpose
from suwapath.contracts.ids import PatientId, TraceId, UserId


class AuditEntryDraft(Contract):
    actor_kind: PrincipalKind
    actor_id: str | None
    agent_kind: AgentKind | None = None
    acting_for: UserId | None = None
    patient_id: PatientId | None = None
    action: str  # e.g. "record.read", "tool.call", "consent.revoke", "private_session.started"
    resource_type: str | None = None
    resource_id: str | None = None
    purpose: Purpose | None = None
    outcome: Literal["allow", "deny", "error"]
    reason: str | None = None
    trace_id: TraceId
    metadata: dict[str, Any] = Field(default_factory=dict)  # no PHI


class AuditEntry(AuditEntryDraft):
    id: str
    at: datetime
    prev_hash: str
    hash: str


class AuditQuery(Contract):
    patient_id: PatientId | None = None
    actor_id: str | None = None
    action: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    trace_id: TraceId | None = None


class AuditLog(Protocol):
    async def record(self, entry: AuditEntryDraft) -> None:
        """Appends inside the caller's unit of work when one is active (AUD-5)."""
        ...

    async def search(
        self, query: AuditQuery, cursor: str | None, limit: int
    ) -> Page[AuditEntry]: ...

    async def verify_chain(self) -> bool: ...
