"""Doctor routes of the `agents` module (spec 10, A05). Thin: access is checked by the case service
for the doctor's principal (treating relationship) on every call; a case the doctor may not see
is 404 (CON-11). The queue stays a stub; U06 lists cases through `GET /cases`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from pydantic import Field
from sqlalchemy import select

from suwapath.contracts.agents import AgentJob
from suwapath.contracts.briefs import Brief
from suwapath.contracts.cases import CaseEventDraft, CaseEventKind
from suwapath.contracts.common import Contract
from suwapath.contracts.enums import AgentKind, MessageStatus, Purpose, Role
from suwapath.contracts.errors import NotFound, ValidationFailed
from suwapath.contracts.ids import CaseId, JobId, MessageId, PatientId
from suwapath.contracts.principal import Principal
from suwapath.modules.agents.adapters import briefs
from suwapath.modules.agents.adapters.models import AgentMessageRow
from suwapath.modules.agents.application import messages
from suwapath.modules.agents.application.context import Dependencies
from suwapath.platform.http.auth import require
from suwapath.platform.http.stubs import StubRoute, stub_router
from suwapath.platform.ids import new_id
from suwapath.platform.jobs import queue
from suwapath.platform.uow import UnitOfWorkFactory

Doctor = Annotated[Principal, Depends(require(Role.DOCTOR, purpose=Purpose.TREATMENT))]
CasePath = Annotated[CaseId, Path()]
DraftPath = Annotated[MessageId, Path()]
CODE = r"^[a-z][a-z0-9_]{1,59}$"


class Notes(Contract):
    text: str = Field(min_length=1, max_length=4000)


class FactRequest(Contract):
    fact_key: str = Field(pattern=CODE)


class Decision(Contract):
    decision: Literal["approved", "edited", "rejected"]
    text: str | None = Field(default=None, min_length=1, max_length=4000)  # required for edited


class Accepted(Contract):
    id: str


class ApprovalItem(Contract):
    draft_id: MessageId
    case_id: CaseId
    text: str
    language: str
    created_at: datetime


QUEUE = StubRoute("GET", "/doctor/queue", (Role.DOCTOR,), "Today's patients", "U06")


def build_router(
    uow_factory: UnitOfWorkFactory, dependencies: Callable[[], Dependencies]
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["agents"])

    async def _visible(principal: Principal, case_id: CaseId) -> tuple[CaseId, PatientId, int]:
        state = await dependencies().cases.get(principal, case_id)
        return state.id, state.patient_id, state.version

    async def _enqueue(
        principal: Principal, case_id: CaseId, patient_id: UUID, data: dict[str, str]
    ) -> None:
        async with uow_factory() as uow:
            queue.enqueue(
                uow,
                AgentJob(
                    id=JobId(new_id()),
                    agent_kind=AgentKind.DOCTOR_ASSISTANT,
                    case_id=case_id,
                    patient_id=patient_id,
                    trigger_event_id=None,
                    acting_for=principal.user_id,
                    purpose=Purpose.DOCTOR_BRIEF,
                    input=data,
                    trace_id=principal.trace_id,
                ),
            )
            await uow.commit()

    @router.get(
        "/doctor/cases/{case_id}/brief",
        summary="Sourced brief",
        operation_id="agents_get_doctor_cases_case_id_brief",
    )
    async def get_brief(case_id: CasePath, principal: Doctor) -> Brief:
        await _visible(principal, case_id)
        assert principal.user_id is not None
        async with uow_factory() as uow:
            brief = await briefs.latest_brief(uow, case_id, principal.user_id)
        if brief is None:
            raise NotFound()
        return brief

    @router.post(
        "/doctor/cases/{case_id}/notes",
        status_code=202,
        summary="Notes for the reply",
        operation_id="agents_post_doctor_cases_case_id_notes",
    )
    async def post_notes(case_id: CasePath, body: Notes, principal: Doctor) -> Accepted:
        case, patient, _ = await _visible(principal, case_id)
        assert principal.user_id is not None
        async with uow_factory() as uow:
            note_id = await briefs.add_note(
                uow,
                case_id=case,
                patient_id=patient,
                author_id=principal.user_id,
                text=body.text,
                at=uow.clock.now(),
            )
            await uow.commit()
        await _enqueue(principal, case, patient, {"trigger": "notes", "notes_id": str(note_id)})
        return Accepted(id=str(note_id))

    @router.post(
        "/doctor/cases/{case_id}/request-fact",
        status_code=202,
        summary="Ask for a missing fact",
        operation_id="agents_post_doctor_cases_case_id_request_fact",
    )
    async def request_fact(case_id: CasePath, body: FactRequest, principal: Doctor) -> Accepted:
        case, patient, _ = await _visible(principal, case_id)
        await _enqueue(
            principal, case, patient, {"trigger": "request_fact", "fact_key": body.fact_key}
        )
        return Accepted(id=body.fact_key)

    @router.get(
        "/doctor/approvals",
        summary="Drafts waiting for approval",
        operation_id="agents_get_doctor_approvals",
    )
    async def approvals(principal: Doctor) -> list[ApprovalItem]:
        async with uow_factory() as uow:
            rows = (
                await uow.session.execute(
                    select(AgentMessageRow)
                    .where(
                        AgentMessageRow.intent == "reply_draft",
                        AgentMessageRow.acting_for == principal.user_id,
                        AgentMessageRow.status == MessageStatus.HELD.value,
                    )
                    .order_by(AgentMessageRow.created_at)
                    .limit(100)
                )
            ).scalars()
            drafts = [(messages.to_message(r), r.created_at) for r in rows]
        return [
            ApprovalItem(
                draft_id=d.message_id,
                case_id=d.case_id,
                text=str(d.body["text"]),
                language=str(d.body["language"]),
                created_at=created_at,
            )
            for d, created_at in drafts
        ]

    @router.post(
        "/doctor/approvals/{draft_id}",
        status_code=202,
        summary="Approve, edit or reject",
        operation_id="agents_post_doctor_approvals_draft_id",
    )
    async def decide(draft_id: DraftPath, body: Decision, principal: Doctor) -> Accepted:
        if body.decision == "edited" and not body.text:
            raise ValidationFailed("an edited reply needs its text", fields=["text"])
        async with uow_factory() as uow:
            draft = await messages.load(uow, draft_id)
        if (
            draft is None
            or draft.status is not MessageStatus.HELD
            or draft.acting_for != principal.user_id
        ):
            raise NotFound()
        case, patient, version = await _visible(principal, draft.case_id)
        assert principal.user_id is not None
        async with uow_factory() as uow:
            if body.decision == "edited" and body.text:
                await briefs.add_note(
                    uow,
                    case_id=case,
                    patient_id=patient,
                    author_id=principal.user_id,
                    text=body.text,
                    at=uow.clock.now(),
                    draft_id=draft_id,
                )
            # Invariant 6: the doctor, not an agent, records the decision.
            await dependencies().cases.append(
                principal,
                case,
                [
                    CaseEventDraft(
                        kind=CaseEventKind.DOCTOR_DECISION_RECORDED,
                        data={"draft_id": str(draft_id), "decision": body.decision},
                        actor=Role.DOCTOR.value,
                    )
                ],
                version,
            )
            await uow.commit()
        return Accepted(id=str(draft_id))

    return router


def queue_stub() -> APIRouter:
    return stub_router("agents", [QUEUE])
