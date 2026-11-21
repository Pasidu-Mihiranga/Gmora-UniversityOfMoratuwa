"""cases: the case aggregate, its event log, timeline and streams."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from suwapath.contracts.cases import CaseEventDraft, CaseState
from suwapath.contracts.common import Contract, Page, UntrustedText
from suwapath.contracts.enums import Language
from suwapath.contracts.ids import CaseId, PatientId, ResourceId
from suwapath.contracts.principal import Principal


class TimelineItem(Contract):
    id: str
    case_id: CaseId
    at: datetime
    kind: Literal["patient_message", "agent_message", "event", "booking", "alert", "decision"]
    actor: str
    summary_key: str
    params: dict[str, Any]  # filtered by the viewer's access
    simulated: bool = False


class CaseService(Protocol):
    async def open(
        self,
        principal: Principal,
        patient_id: PatientId,
        first_message: UntrustedText,
        language: Language,
    ) -> CaseState: ...

    async def add_patient_message(
        self, principal: Principal, case_id: CaseId, message: UntrustedText
    ) -> ResourceId:
        """Stores a Communication; emits cases.patient_message_received."""
        ...

    async def get(self, principal: Principal, case_id: CaseId) -> CaseState:
        """Raises NotFound when the principal may not see the case (CON-11)."""
        ...

    async def append(
        self,
        principal: Principal,
        case_id: CaseId,
        drafts: list[CaseEventDraft],
        expected_version: int,
    ) -> CaseState:
        """Raises VersionConflict or InvalidTransition (CON-10, DB-6)."""
        ...

    async def timeline(
        self, principal: Principal, case_id: CaseId, cursor: str | None, limit: int
    ) -> Page[TimelineItem]: ...

    async def deliver_communication(
        self, case_id: CaseId, recipient: str, payload: str, simulated: bool
    ) -> ResourceId:
        """Called only by the verified delivery path (AGT-10)."""
        ...
