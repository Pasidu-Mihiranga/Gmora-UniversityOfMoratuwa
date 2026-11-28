"""`CaseService` on Postgres: the event log, the case row, the timeline and communications.

Every write joins the caller's unit of work (ADR-0029), so a case event, its integration event,
its timers and the caller's own writes commit together (EVT-1, EVT-2). Optimistic locking: the
expected version is checked, and the unique `(case_id, version)` constraint turns a race into
`VersionConflict` (DB-6).
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from suwapath.contracts.cases import CaseEventDraft, CaseEventKind, CaseState
from suwapath.contracts.common import Page, UntrustedText
from suwapath.contracts.enums import (
    AgentKind,
    CaseStatus,
    DataCategory,
    Language,
    PrincipalKind,
    Role,
    Urgency,
)
from suwapath.contracts.errors import AccessDenied, InvalidTransition, NotFound, VersionConflict
from suwapath.contracts.events import EventPayload
from suwapath.contracts.events.catalog import (
    CaseOpened,
    DoctorApprovalRequested,
    DoctorDecisionRecorded,
    HandedToHuman,
    PatientMessageReceived,
    RedFlagRaised,
    ReplyDelivered,
    UrgencyAssessed,
)
from suwapath.contracts.ids import CaseId, PatientId, ResourceId, TraceId
from suwapath.contracts.ports.audit import AuditEntryDraft
from suwapath.contracts.ports.cases import TimelineItem
from suwapath.contracts.principal import Principal
from suwapath.modules.cases.adapters.models import (
    CaseEventRow,
    CaseRow,
    CommunicationRow,
    TimelineRow,
)
from suwapath.modules.cases.application.access import (
    PolicySource,
    check_writer,
    filter_state,
    visible,
)
from suwapath.modules.cases.domain.aggregate import Case, apply, category_of, open_case, replay
from suwapath.platform.http.streams import notify_stream
from suwapath.platform.ids import new_id
from suwapath.platform.tracing import current_trace_id
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)
K = CaseEventKind
APPROVAL_TIMER = "approval_timeout"
QUESTION_SOURCE = "patient_question"  # UntrustedText.source of an "Ask my doctor" message
DOCTOR = "doctor"
MESSAGE_RETRIES = 3
TIMELINE_KIND: dict[CaseEventKind, str] = {
    K.PATIENT_MESSAGE: "patient_message",
    K.APPOINTMENT_BOOKED: "booking",
    K.GUARDIAN_UPDATED: "alert",
    K.DOCTOR_DECISION_RECORDED: "decision",
}
# The event data fields a timeline item may show (codes and IDs only, never free text).
TIMELINE_PARAMS: dict[CaseEventKind, tuple[str, ...]] = {
    K.CASE_OPENED: ("opened_by", "reason", "communication_id"),
    K.PATIENT_MESSAGE: ("communication_id",),
    K.URGENCY_ASSESSED: ("urgency",),
    K.RED_FLAG_RAISED: ("rule_ids",),
    K.ROUTE_SUGGESTED: ("primary_specialty", "urgency"),
    K.APPOINTMENT_BOOKED: ("appointment_id",),
    K.GUARDIAN_UPDATED: ("status", "last_alert_level"),
    K.DOCTOR_DECISION_RECORDED: ("decision",),
    K.REPLY_DELIVERED: ("communication_id",),
    K.HANDED_TO_HUMAN: ("reason",),
}
RECIPIENT_CATEGORY: dict[str, DataCategory] = {"guardian": DataCategory.ADHERENCE}


class SqlCaseService:
    def __init__(self, uow_factory: UnitOfWorkFactory, policy: PolicySource) -> None:
        self._uow = uow_factory
        self._policy = policy

    # ----- port: CaseService ------------------------------------------------------------------

    async def open(
        self,
        principal: Principal,
        patient_id: PatientId,
        first_message: UntrustedText,
        language: Language,
    ) -> CaseState:
        opened_by = _opened_by(principal)
        from_patient = first_message.source == "patient_message"
        reason = None if from_patient else first_message.value
        async with self._uow.join() as uow:
            case_id = CaseId(new_id())
            communication_id = ResourceId(new_id()) if from_patient else None
            case, data = open_case(
                case_id,
                {
                    "patient_id": str(patient_id),
                    "language": language.value,
                    "opened_by": opened_by,
                    "reason": reason,
                    "communication_id": str(communication_id) if communication_id else None,
                },
                uow.clock.now(),
            )
            await visible(self._policy, principal, case.state, case.categories)
            uow.session.add(
                CaseRow(
                    id=case_id,
                    patient_id=case.state.patient_id,
                    status=case.state.status.value,
                    language=language.value,
                    version=1,
                    simulated=case.state.simulated,
                )
            )
            await uow.session.flush()
            if communication_id is not None:
                self._store_patient_text(uow, case, communication_id, first_message)
                await uow.session.flush()
            await self._record(uow, case, K.CASE_OPENED, data, _actor(principal), None)
            await _audit(uow, principal, case, "case.open")
            await uow.session.flush()
        return case.state

    async def add_patient_message(
        self, principal: Principal, case_id: CaseId, message: UntrustedText
    ) -> ResourceId:
        if principal.kind is not PrincipalKind.USER or principal.role is not Role.PATIENT:
            raise AccessDenied()
        for attempt in range(MESSAGE_RETRIES):
            try:
                async with self._uow.join() as uow:
                    case = await self._load(uow, case_id)
                    await visible(self._policy, principal, case.state, case.categories)
                    _, communication_id = await self._patient_message(uow, case, principal, message)
                    return communication_id
            except VersionConflict:
                if attempt == MESSAGE_RETRIES - 1:
                    raise
        raise VersionConflict()  # pragma: no cover

    async def get(self, principal: Principal, case_id: CaseId) -> CaseState:
        async with self._uow.join() as uow:
            case = await self._load(uow, case_id)
        allowed = await visible(self._policy, principal, case.state, case.categories)
        return filter_state(case.state, allowed)

    async def append(
        self,
        principal: Principal,
        case_id: CaseId,
        drafts: list[CaseEventDraft],
        expected_version: int,
    ) -> CaseState:
        check_writer(principal)
        _check_doctor_decisions(principal, drafts)
        async with self._uow.join() as uow:
            case = await self._load(uow, case_id)
            await visible(self._policy, principal, case.state, case.categories)
            if case.state.version != expected_version:
                raise VersionConflict()
            case = await self.append_events(uow, case, drafts, principal)
            await _audit(uow, principal, case, "case.append")
        return case.state

    async def timeline(
        self, principal: Principal, case_id: CaseId, cursor: str | None, limit: int
    ) -> Page[TimelineItem]:
        limit = max(1, min(limit, 100))
        async with self._uow.join() as uow:
            case = await self._load(uow, case_id)
            allowed = await visible(self._policy, principal, case.state, case.categories)
            query = (
                select(TimelineRow)
                .where(
                    TimelineRow.case_id == case_id,
                    TimelineRow.category.in_(sorted(c.value for c in allowed)),
                )
                .order_by(TimelineRow.seq)
                .limit(limit + 1)
            )
            if cursor:
                query = query.where(TimelineRow.seq > _decode_cursor(cursor))
            rows = list((await uow.session.execute(query)).scalars())
            texts = await _texts(uow, rows[:limit])
        items = [_item(row, texts) for row in rows[:limit]]
        next_cursor = _encode_cursor(rows[limit - 1].seq) if len(rows) > limit else None
        return Page(items=items, next_cursor=next_cursor)

    async def deliver_communication(
        self, case_id: CaseId, recipient: str, payload: str, simulated: bool
    ) -> ResourceId:
        """Only the verified delivery path calls this (AGT-10); it carries no principal."""
        async with self._uow.join() as uow:
            case = await self._load(uow, case_id)
            communication_id = ResourceId(new_id())
            uow.session.add(
                CommunicationRow(
                    id=communication_id,
                    case_id=case_id,
                    patient_id=case.state.patient_id,
                    sender="agent",
                    recipient=recipient[:40],
                    payload_enc=payload.encode(),  # encrypted with the patient key once M04 lands
                    simulated=simulated,
                )
            )
            uow.session.add(
                TimelineRow(
                    id=new_id(),
                    case_id=case_id,
                    category=RECIPIENT_CATEGORY.get(recipient, DataCategory.CONVERSATIONS).value,
                    at=uow.clock.now(),
                    kind="agent_message",
                    actor="agent",
                    summary_key=f"case.message_to_{_code(recipient)}",
                    params={"communication_id": str(communication_id)},
                    simulated=simulated,
                )
            )
            await notify_stream(uow, stream_key(case_id))
            await uow.session.flush()
        return communication_id

    # ----- module-internal --------------------------------------------------------------------

    async def latest_patient_text(self, case_id: CaseId) -> UntrustedText | None:
        """For the patient companion; the agent runtime checks consent before calling it."""
        async with self._uow.join() as uow:
            row = (
                await uow.session.execute(
                    select(CommunicationRow.payload_enc)
                    .where(
                        CommunicationRow.case_id == case_id, CommunicationRow.sender == "patient"
                    )
                    .order_by(CommunicationRow.created_at.desc(), CommunicationRow.id.desc())
                    .limit(1)
                )
            ).scalar()
        return UntrustedText(value=row.decode(), source="patient_message") if row else None

    async def timeline_since(
        self, principal: Principal, case_id: CaseId, after_seq: int | None, limit: int = 100
    ) -> tuple[CaseState, list[tuple[int, TimelineItem]], int]:
        """For the case stream: the viewer's state, the items after `after_seq` they may see,
        and the position to continue from. `after_seq=None` means from now (no replay).
        Access is decided again on every call (SEC-12); NotFound once it ends."""
        async with self._uow.join() as uow:
            case = await self._load(uow, case_id)
            allowed = await visible(self._policy, principal, case.state, case.categories)
            if after_seq is None:
                position = (
                    await uow.session.execute(
                        select(func.coalesce(func.max(TimelineRow.seq), 0)).where(
                            TimelineRow.case_id == case_id
                        )
                    )
                ).scalar_one()
                return filter_state(case.state, allowed), [], int(position)
            rows = list(
                (
                    await uow.session.execute(
                        select(TimelineRow)
                        .where(
                            TimelineRow.case_id == case_id,
                            TimelineRow.seq > after_seq,
                            TimelineRow.category.in_(sorted(c.value for c in allowed)),
                        )
                        .order_by(TimelineRow.seq)
                        .limit(limit)
                    )
                ).scalars()
            )
            texts = await _texts(uow, rows)
        items = [(row.seq, _item(row, texts)) for row in rows]
        position = rows[-1].seq if rows else after_seq
        return filter_state(case.state, allowed), items, position

    async def latest_patient_message(
        self, case_id: CaseId
    ) -> tuple[ResourceId, UntrustedText] | None:
        """The latest patient message and its communication ID, the source of what it says."""
        async with self._uow.join() as uow:
            row = (
                await uow.session.execute(
                    select(
                        CommunicationRow.id,
                        CommunicationRow.payload_enc,
                        CommunicationRow.recipient,
                    )
                    .where(
                        CommunicationRow.case_id == case_id, CommunicationRow.sender == "patient"
                    )
                    .order_by(CommunicationRow.created_at.desc(), CommunicationRow.id.desc())
                    .limit(1)
                )
            ).first()
        if row is None:
            return None
        source = QUESTION_SOURCE if row.recipient == DOCTOR else "patient_message"
        return ResourceId(row.id), UntrustedText(value=row.payload_enc.decode(), source=source)

    async def patient_of(self, case_id: CaseId) -> PatientId:
        async with self._uow.join() as uow:
            patient = (
                await uow.session.execute(select(CaseRow.patient_id).where(CaseRow.id == case_id))
            ).scalar()
        if patient is None:
            raise NotFound()
        return PatientId(patient)

    async def cases_of(
        self,
        principal: Principal,
        patient_ids: Sequence[PatientId] | None,
        status: CaseStatus | None,
        cursor: str | None,
        limit: int,
    ) -> Page[CaseState]:
        """Cases the principal may see, newest first. `patient_ids` narrows the search."""
        limit = max(1, min(limit, 50))
        async with self._uow.join() as uow:
            query = select(CaseRow.id).order_by(CaseRow.id.desc()).limit(limit * 4)
            if patient_ids is not None:
                query = query.where(CaseRow.patient_id.in_(list(patient_ids)))
            if status is not None:
                query = query.where(CaseRow.status == status.value)
            if cursor:
                query = query.where(CaseRow.id < UUID(_decode_text(cursor)))
            ids = list((await uow.session.execute(query)).scalars())
            states: list[CaseState] = []
            last: UUID | None = None
            for case_id in ids:
                last = case_id
                case = await self._load(uow, CaseId(case_id))
                try:
                    allowed = await visible(self._policy, principal, case.state, case.categories)
                except NotFound:
                    continue
                states.append(filter_state(case.state, allowed))
                if len(states) == limit:
                    break
        more = len(states) == limit or len(ids) == limit * 4
        next_cursor = _encode_text(str(last)) if more and last is not None else None
        return Page(items=states, next_cursor=next_cursor)

    async def append_events(
        self,
        uow: UnitOfWork,
        case: Case,
        drafts: Sequence[CaseEventDraft],
        principal: Principal | None,
    ) -> Case:
        """Apply and store events on a loaded case. Used by `append` and by subscribers."""
        start = case.state.version
        for draft in drafts:
            try:
                case, data = apply(case, draft.kind, draft.data, uow.clock.now())
            except InvalidTransition:
                log.warning(
                    "case event refused",
                    extra={
                        "case_id": str(case.state.id),
                        "kind": draft.kind.value,
                        "status": case.state.status.value,
                    },
                )
                raise
            await self._record(uow, case, draft.kind, data, draft.actor[:40], principal)
        try:
            await uow.session.flush()  # the (case_id, version) constraint catches a racing writer
        except IntegrityError:
            raise VersionConflict() from None
        changed = await uow.session.execute(
            update(CaseRow)
            .where(CaseRow.id == case.state.id, CaseRow.version == start)
            .values(
                status=case.state.status.value,
                urgency=case.state.urgency.value if case.state.urgency else None,
                version=case.state.version,
                updated_at=uow.clock.now(),
                closed_at=uow.clock.now() if case.state.status is CaseStatus.CLOSED else None,
            )
            .returning(CaseRow.id)
        )
        if changed.scalar_one_or_none() is None:
            raise VersionConflict()
        return case

    async def load(self, uow: UnitOfWork, case_id: CaseId) -> Case:
        return await self._load(uow, case_id)

    async def _load(self, uow: UnitOfWork, case_id: CaseId) -> Case:
        rows = (
            await uow.session.execute(
                select(
                    CaseEventRow.kind, CaseEventRow.data, CaseEventRow.at, CaseEventRow.simulated
                )
                .where(CaseEventRow.case_id == case_id)
                .order_by(CaseEventRow.version)
            )
        ).all()
        if not rows:
            raise NotFound()
        case = replay(case_id, [(K(r.kind), r.data, r.at) for r in rows])
        if rows[0].simulated:
            case = case.model_copy(
                update={"state": case.state.model_copy(update={"simulated": True})}
            )
        return case

    async def _patient_message(
        self, uow: UnitOfWork, case: Case, principal: Principal, message: UntrustedText
    ) -> tuple[Case, ResourceId]:
        communication_id = ResourceId(new_id())
        self._store_patient_text(uow, case, communication_id, message)
        await uow.session.flush()
        draft = CaseEventDraft(
            kind=K.PATIENT_MESSAGE,
            data={"communication_id": str(communication_id)},
            actor="patient",
        )
        return await self.append_events(uow, case, [draft], principal), communication_id

    def _store_patient_text(
        self, uow: UnitOfWork, case: Case, communication_id: ResourceId, message: UntrustedText
    ) -> None:
        uow.session.add(
            CommunicationRow(
                id=communication_id,
                case_id=case.state.id,
                patient_id=case.state.patient_id,
                sender="patient",
                recipient=DOCTOR if message.source == QUESTION_SOURCE else "patient_companion",
                payload_enc=message.value.encode(),  # encrypted with the patient key once M04 lands
                simulated=case.state.simulated,
            )
        )

    async def _record(
        self,
        uow: UnitOfWork,
        case: Case,
        kind: CaseEventKind,
        data: dict[str, Any],
        actor: str,
        principal: Principal | None,
    ) -> None:
        state = case.state
        now = uow.clock.now()
        uow.session.add(
            CaseEventRow(
                id=new_id(),
                case_id=state.id,
                version=state.version,
                kind=kind.value,
                data=data,
                actor=actor,
                at=now,
                simulated=state.simulated,
            )
        )
        uow.session.add(
            TimelineRow(
                id=new_id(),
                case_id=state.id,
                category=category_of(case, kind).value,
                at=now,
                kind=_timeline_kind(kind, data),
                actor=actor,
                summary_key=f"case.{kind.value}",
                params={k: data[k] for k in TIMELINE_PARAMS.get(kind, ()) if k in data},
                simulated=state.simulated,
            )
        )
        try:
            # Flush now, before any other query can autoflush it: the (case_id, version)
            # constraint must surface here, as a VersionConflict, whoever writes next (DB-6).
            await uow.session.flush()
        except IntegrityError:
            raise VersionConflict() from None
        await notify_stream(uow, stream_key(state.id))  # delivered on commit (API-21)
        payload = _integration_event(case, kind, data)
        if payload is not None:
            uow.publish(
                payload,
                aggregate_id=str(state.id),
                patient_id=state.patient_id,
                correlation_id=state.id,
            )
        if kind is K.DOCTOR_APPROVAL_REQUESTED and state.pending_approval is not None:
            await uow.schedule_timer(
                kind=APPROVAL_TIMER,
                due_at=state.pending_approval.due_at,
                ref_type="case",
                ref_id=str(state.id),
                dedupe_key=f"approval:{data['draft_id']}",
                payload={"draft_id": data["draft_id"], "trace_id": current_trace_id()},
            )
        if kind is K.DOCTOR_DECISION_RECORDED:
            await uow.cancel_timer(f"approval:{data['draft_id']}")


def _integration_event(
    case: Case, kind: CaseEventKind, data: dict[str, Any]
) -> EventPayload | None:
    state = case.state
    match kind:
        case K.CASE_OPENED:
            return CaseOpened(
                case_id=state.id, patient_id=state.patient_id, opened_by=data["opened_by"]
            )
        case K.PATIENT_MESSAGE:
            return PatientMessageReceived(
                case_id=state.id, communication_id=ResourceId(UUID(data["communication_id"]))
            )
        case K.URGENCY_ASSESSED:
            return UrgencyAssessed(
                case_id=state.id,
                urgency=state.urgency or Urgency(data["urgency"]),
                floor=state.urgency_floor,
            )
        case K.RED_FLAG_RAISED:
            return RedFlagRaised(
                case_id=state.id,
                patient_id=state.patient_id,
                rule_ids=tuple(data["rule_ids"]),
                urgency=Urgency.EMERGENCY,
            )
        case K.DOCTOR_APPROVAL_REQUESTED:
            assert state.pending_approval is not None
            return DoctorApprovalRequested(
                case_id=state.id,
                draft_id=state.pending_approval.draft_id,
                practitioner_id=state.pending_approval.practitioner_id,
                due_at=state.pending_approval.due_at,
            )
        case K.DOCTOR_DECISION_RECORDED:
            return DoctorDecisionRecorded(
                case_id=state.id, draft_id=data["draft_id"], decision=data["decision"]
            )
        case K.REPLY_DELIVERED:
            return ReplyDelivered(
                case_id=state.id, communication_id=ResourceId(UUID(data["communication_id"]))
            )
        case K.HANDED_TO_HUMAN:
            return HandedToHuman(case_id=state.id, reason=data["reason"])
    return None


def stream_key(case_id: CaseId) -> str:
    return f"case:{case_id}"


def _timeline_kind(kind: CaseEventKind, data: dict[str, Any]) -> str:
    if kind is K.CASE_OPENED and data.get("communication_id"):
        return "patient_message"  # the case opened with the patient's first message
    return TIMELINE_KIND.get(kind, "event")


def _check_doctor_decisions(principal: Principal, drafts: Sequence[CaseEventDraft]) -> None:
    """Invariant 6: only a doctor records a doctor's decision, and a doctor records only that."""
    is_doctor = principal.kind is PrincipalKind.USER and principal.role is Role.DOCTOR
    for draft in drafts:
        if (draft.kind is K.DOCTOR_DECISION_RECORDED) != is_doctor:
            raise AccessDenied()


def _opened_by(principal: Principal) -> Literal["patient", "guardian_agent", "system"]:
    if principal.kind is PrincipalKind.USER:
        if principal.role is not Role.PATIENT:
            raise AccessDenied()
        return "patient"
    if principal.kind is PrincipalKind.AGENT and principal.agent_kind is AgentKind.GUARDIAN_AGENT:
        return "guardian_agent"
    return "system"


def _actor(principal: Principal) -> str:
    if principal.kind is PrincipalKind.AGENT and principal.agent_kind is not None:
        return principal.agent_kind.value
    if principal.kind is PrincipalKind.USER and principal.role is not None:
        return principal.role.value
    return "system"


async def _audit(uow: UnitOfWork, principal: Principal, case: Case, action: str) -> None:
    await uow.audit(
        AuditEntryDraft(
            actor_kind=principal.kind,
            actor_id=str(principal.user_id) if principal.user_id else None,
            agent_kind=principal.agent_kind,
            acting_for=principal.acting_for,
            patient_id=case.state.patient_id,
            action=action,
            resource_type="Case",
            resource_id=str(case.state.id),
            purpose=principal.purpose,
            outcome="allow",
            trace_id=TraceId(principal.trace_id),
            metadata={"version": case.state.version, "status": case.state.status.value},
        )
    )


async def _texts(uow: UnitOfWork, rows: Sequence[TimelineRow]) -> dict[str, tuple[str, str]]:
    ids = [UUID(r.params["communication_id"]) for r in rows if "communication_id" in r.params]
    if not ids:
        return {}
    found = await uow.session.execute(
        select(CommunicationRow.id, CommunicationRow.payload_enc, CommunicationRow.recipient).where(
            CommunicationRow.id.in_(ids)
        )
    )
    return {str(i): (payload.decode(), to) for i, payload, to in found.all()}


def _item(row: TimelineRow, texts: dict[str, tuple[str, str]]) -> TimelineItem:
    params = dict(row.params)
    found = texts.get(params.get("communication_id", ""))
    if found is not None and row.kind in ("patient_message", "agent_message"):
        params["text"] = found[0]  # the row is already filtered to a category the viewer may see
        if found[1] == DOCTOR:
            params["to"] = DOCTOR  # "Ask my doctor": the treating doctor reads it here
    return TimelineItem(
        id=str(row.id),
        case_id=CaseId(row.case_id),
        at=row.at,
        kind=row.kind,  # stored values come from TIMELINE_KIND
        actor=row.actor,
        summary_key=row.summary_key,
        params=params,
        simulated=row.simulated,
    )


def _code(value: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in value.lower())[:30]


def _encode_cursor(seq: int) -> str:
    return _encode_text(str(seq))


def _decode_cursor(cursor: str) -> int:
    try:
        return int(_decode_text(cursor))
    except ValueError:
        raise NotFound() from None


def _encode_text(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _decode_text(cursor: str) -> str:
    try:
        return base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        raise NotFound() from None
