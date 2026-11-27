"""HTTP routes for the `cases` module (spec 10 section 2, "Cases and companion").

Routes are thin: they build no access rules of their own; `SqlCaseService` checks access for the
principal on every call. The stream route stays a stub until stream tickets exist (M10 part 2).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import Field

from suwapath.contracts.cases import CaseState
from suwapath.contracts.common import Contract, Page, UntrustedText
from suwapath.contracts.enums import CaseStatus, Language, Purpose, Role
from suwapath.contracts.errors import NotFound
from suwapath.contracts.ids import CaseId, ResourceId
from suwapath.contracts.ports.cases import TimelineItem
from suwapath.contracts.principal import Principal
from suwapath.modules.cases.application.service import SqlCaseService
from suwapath.platform.http.auth import require
from suwapath.platform.http.stubs import StubRoute, stub_router

PURPOSES = {Role.GUARDIAN: Purpose.GUARDIAN_MONITORING, Role.DOCTOR: Purpose.TREATMENT}
Patient = Annotated[Principal, Depends(require(Role.PATIENT, purpose=Purpose.CARE_NAVIGATION))]
Viewer = Annotated[
    Principal,
    Depends(require(Role.PATIENT, Role.GUARDIAN, Role.DOCTOR, purposes=PURPOSES)),
]
CasePath = Annotated[CaseId, Path()]
MAX_MESSAGE = 4000


class OpenCase(Contract):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE)
    language: Language


class PatientMessage(Contract):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE)


class MessageAccepted(Contract):
    communication_id: ResourceId


STREAM = StubRoute(
    "GET",
    "/cases/{case_id}/stream",
    (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
    "Case events (SSE)",
    "M10",
)


def build_router(service: SqlCaseService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["cases"])

    @router.post(
        "/cases",
        status_code=201,
        summary="Open a case with the first message",
        operation_id="cases_post_cases",
    )
    async def open_case(body: OpenCase, principal: Patient) -> CaseState:
        if principal.patient_scope is None:
            raise NotFound()
        return await service.open(
            principal,
            principal.patient_scope,
            UntrustedText(value=body.text, source="patient_message"),
            body.language,
        )

    @router.get("/cases", summary="My cases", operation_id="cases_get_cases")
    async def my_cases(
        principal: Viewer,
        status: CaseStatus | None = None,
        cursor: Annotated[str | None, Query(max_length=200)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> Page[CaseState]:
        patients = [principal.patient_scope] if principal.patient_scope else None
        return await service.cases_of(principal, patients, status, cursor, limit)

    @router.get("/cases/{case_id}", summary="Case state", operation_id="cases_get_cases_case_id")
    async def case_state(case_id: CasePath, principal: Viewer) -> CaseState:
        return await service.get(principal, case_id)

    @router.get(
        "/cases/{case_id}/timeline",
        summary="Case timeline",
        operation_id="cases_get_cases_case_id_timeline",
    )
    async def timeline(
        case_id: CasePath,
        principal: Viewer,
        cursor: Annotated[str | None, Query(max_length=200)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> Page[TimelineItem]:
        return await service.timeline(principal, case_id, cursor, limit)

    @router.post(
        "/cases/{case_id}/messages",
        status_code=202,
        summary="Send a message",
        operation_id="cases_post_cases_case_id_messages",
    )
    async def send_message(
        case_id: CasePath, body: PatientMessage, principal: Patient
    ) -> MessageAccepted:
        communication_id = await service.add_patient_message(
            principal, case_id, UntrustedText(value=body.text, source="patient_message")
        )
        return MessageAccepted(communication_id=communication_id)

    return router


def stream_stub() -> APIRouter:
    return stub_router("cases", [STREAM])
