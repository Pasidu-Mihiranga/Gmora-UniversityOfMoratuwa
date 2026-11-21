"""memory: case summaries, confirmed learned facts, preference suggestions (MEM-1 to MEM-12)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import DataCategory, Purpose
from suwapath.contracts.ids import CaseId, EventId, PatientId, ResourceId, UserId
from suwapath.contracts.principal import Principal


class SummaryLine(Contract):
    key: str  # template line key, e.g. "summary.reason", rendered per language
    params: dict[str, str | int | float | bool]
    source_event_ids: tuple[EventId, ...]


class CaseSummary(Contract):
    """MEM-5: derived record, never clinician-authored, never a clinical resource."""

    id: ResourceId
    case_id: CaseId
    patient_id: PatientId
    closed_at: datetime
    lines: tuple[SummaryLine, ...]
    concepts: frozenset[str] = frozenset()
    specialty: str | None = None
    label_key: Literal["summary.not_a_medical_record"] = "summary.not_a_medical_record"


class MemoryItem(Contract):
    id: ResourceId
    owner_id: UserId
    subject_patient_id: PatientId | None
    kind: Literal["case_summary", "learned_fact", "preference_suggestion"]
    category: DataCategory
    purpose: Purpose
    content: str
    source_refs: tuple[str, ...]
    status: Literal["proposed", "confirmed", "declined", "deleted"]
    created_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime | None = None
    version: int = 1


class MemoryService(Protocol):
    async def summarize_closed_case(self, case_id: CaseId) -> CaseSummary:
        """Worker entry point on case close; template only, no model (MEM-4)."""
        ...

    async def recent_summaries(
        self, principal: Principal, patient_id: PatientId, concepts: frozenset[str], limit: int = 3
    ) -> list[CaseSummary]: ...

    async def propose(
        self,
        principal: Principal,
        patient_id: PatientId | None,
        kind: Literal["learned_fact", "preference_suggestion"],
        content: str,
        source_refs: tuple[str, ...],
    ) -> MemoryItem:
        """A proposal shown as a confirmation chip; nothing is used until confirmed (MEM-7)."""
        ...

    async def decide(
        self, principal: Principal, item_id: ResourceId, confirm: bool
    ) -> MemoryItem: ...

    async def items(
        self, principal: Principal, patient_id: PatientId | None
    ) -> list[MemoryItem]: ...

    async def delete(self, principal: Principal, item_id: ResourceId) -> None:
        """Propagates to caches and future context (MEM-11)."""
        ...
