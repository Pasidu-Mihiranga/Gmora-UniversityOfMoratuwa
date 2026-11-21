"""reports: lab report upload, extraction, explanation drafts (REP-1 to REP-4, UPL-1 to UPL-7)."""

from __future__ import annotations

from typing import Protocol

from suwapath.contracts.common import Contract, Page
from suwapath.contracts.enums import DocumentStatus
from suwapath.contracts.fhir.resources import Observation
from suwapath.contracts.ids import DocumentId, PatientId, ResourceId
from suwapath.contracts.principal import Principal


class ReportView(Contract):
    report_id: ResourceId
    document_id: DocumentId
    status: DocumentStatus
    observations: tuple[Observation, ...] = ()
    explanation_message_id: str | None = None  # verified explanation delivered via the case


class ReportService(Protocol):
    async def upload(
        self, principal: Principal, patient_id: PatientId, data: bytes, content_type: str
    ) -> ResourceId: ...

    async def get(self, principal: Principal, report_id: ResourceId) -> ReportView: ...

    async def list(
        self, principal: Principal, patient_id: PatientId, cursor: str | None, limit: int
    ) -> Page[ReportView]: ...

    async def process(self, report_id: ResourceId, document_id: DocumentId) -> None:
        """Worker entry point for reports.report_uploaded."""
        ...
