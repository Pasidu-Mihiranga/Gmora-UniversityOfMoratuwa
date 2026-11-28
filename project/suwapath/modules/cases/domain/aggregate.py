"""The case aggregate: validated event data, the state machine and the fold (spec 02 section 5).

Pure functions, no I/O. `apply` either returns the next case and the normalized event data, or
raises `InvalidTransition` (CON-10) or `ValidationFailed`. Replaying stored events through `apply`
rebuilds exactly the same case.

Three rows go beyond the spec table, each needed by a flow the spec describes:
- `in_care` + `AppointmentBooked` stays `in_care` (after `ApprovalTimedOut` the patient is offered
  another doctor and books again);
- `awaiting_doctor` + `DoctorApprovalRequested` is allowed once the doctor rejected the last draft
  (the agent revises the draft);
- `awaiting_doctor` + `DoctorDecisionRecorded` stays `awaiting_doctor` until the reply is delivered.
Events that add information without moving the case (messages, questions, facts, guardian and
brief updates) are allowed in every active state; routes only before or during care.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field, ValidationError

from suwapath.contracts.cases import (
    CaseEventKind,
    CaseFact,
    CaseState,
    GuardianBranchState,
    OpenQuestion,
    PendingApproval,
    RedFlagHit,
    RouteSuggestion,
)
from suwapath.contracts.common import Contract
from suwapath.contracts.enums import (
    AgentKind,
    CaseStatus,
    DataCategory,
    DoctorDecision,
    Language,
    Urgency,
    max_urgency,
)
from suwapath.contracts.errors import InvalidTransition, ValidationFailed
from suwapath.contracts.ids import (
    AppointmentId,
    CaseId,
    MessageId,
    PatientId,
    PractitionerId,
    ResourceId,
)

K = CaseEventKind
S = CaseStatus
CODE = r"^[a-z][a-z0-9_]{0,39}$"
UNVERSIONED = "unversioned"
APPROVAL_TIMEOUT = timedelta(hours=4)  # spec 03 section 6, `approval_timeout`


# ----- Event data (IDs and codes only; patient text lives in communications) --------------------


class _Data(Contract):
    pass


class OpenedData(_Data):
    patient_id: PatientId
    language: Language
    opened_by: Literal["patient", "guardian_agent", "system"]
    reason: str | None = Field(default=None, pattern=CODE)
    communication_id: ResourceId | None = None  # the patient's first message, if any


class EmptyData(_Data):
    pass


class CommunicationData(_Data):
    communication_id: ResourceId


class QuestionData(_Data):
    key: str = Field(pattern=CODE)
    asked_by: AgentKind
    for_role: Literal["patient", "doctor"] = "patient"


class UrgencyData(_Data):
    urgency: Urgency
    floor: Urgency | None = None


class RedFlagData(_Data):
    rule_ids: tuple[str, ...] = Field(min_length=1)
    rules_version: str = UNVERSIONED


class AppointmentData(_Data):
    appointment_id: AppointmentId


class GuardianData(_Data):
    status: Literal["idle", "checking_in", "alerted", "escalated"]
    last_alert_level: Literal["info", "concern", "urgent", "emergency"] | None = None


class BriefData(_Data):
    brief_id: ResourceId


class ApprovalRequestData(_Data):
    draft_id: MessageId
    practitioner_id: PractitionerId
    due_at: datetime


class DecisionData(_Data):
    draft_id: MessageId
    decision: DoctorDecision


class DraftData(_Data):
    draft_id: MessageId


class ReplyData(_Data):
    communication_id: ResourceId
    draft_id: MessageId


class HandedData(_Data):
    reason: Literal[
        "budget", "verifier_unsure", "agent_failure", "revision_limit", "patient_request"
    ]


DATA: dict[CaseEventKind, type[Contract]] = {
    K.CASE_OPENED: OpenedData,
    K.INTAKE_STARTED: EmptyData,
    K.PATIENT_MESSAGE: CommunicationData,
    K.QUESTION_ASKED: QuestionData,
    K.FACT_RECORDED: CaseFact,
    K.URGENCY_ASSESSED: UrgencyData,
    K.RED_FLAG_RAISED: RedFlagData,
    K.ROUTE_SUGGESTED: RouteSuggestion,
    K.APPOINTMENT_BOOKED: AppointmentData,
    K.GUARDIAN_UPDATED: GuardianData,
    K.BRIEF_UPDATED: BriefData,
    K.DOCTOR_APPROVAL_REQUESTED: ApprovalRequestData,
    K.DOCTOR_DECISION_RECORDED: DecisionData,
    K.APPROVAL_TIMED_OUT: DraftData,
    K.REPLY_DELIVERED: ReplyData,
    K.HANDED_TO_HUMAN: HandedData,
    K.CASE_CLOSED: EmptyData,
}


def parse(kind: CaseEventKind, data: dict[str, Any]) -> Contract:
    try:
        return DATA[kind].model_validate(data)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise ValidationFailed("invalid case event data", kind=kind.value, fields=fields) from None


# ----- The state machine ------------------------------------------------------------------------

ACTIVE: frozenset[CaseStatus] = frozenset(S) - {S.CLOSED}
_ANNOTATIONS = (K.PATIENT_MESSAGE, K.QUESTION_ASKED, K.FACT_RECORDED, K.GUARDIAN_UPDATED)

TRANSITIONS: dict[CaseEventKind, dict[CaseStatus, CaseStatus]] = {
    K.INTAKE_STARTED: {S.OPEN: S.INTAKE},
    K.URGENCY_ASSESSED: {S.INTAKE: S.ASSESSED},
    K.RED_FLAG_RAISED: {s: S.EMERGENCY for s in ACTIVE},
    K.APPOINTMENT_BOOKED: {S.ASSESSED: S.IN_CARE, S.IN_CARE: S.IN_CARE},
    K.DOCTOR_APPROVAL_REQUESTED: {
        S.IN_CARE: S.AWAITING_DOCTOR,
        S.AWAITING_DOCTOR: S.AWAITING_DOCTOR,
    },
    K.DOCTOR_DECISION_RECORDED: {S.AWAITING_DOCTOR: S.AWAITING_DOCTOR},
    K.APPROVAL_TIMED_OUT: {S.AWAITING_DOCTOR: S.IN_CARE},
    K.REPLY_DELIVERED: {S.AWAITING_DOCTOR: S.REPLIED},
    K.HANDED_TO_HUMAN: {s: S.HANDED_TO_HUMAN for s in ACTIVE - {S.HANDED_TO_HUMAN}},
    K.CASE_CLOSED: {s: S.CLOSED for s in (S.REPLIED, S.EMERGENCY, S.HANDED_TO_HUMAN)},
    K.ROUTE_SUGGESTED: {s: s for s in (S.INTAKE, S.ASSESSED, S.IN_CARE)},
    K.BRIEF_UPDATED: {s: s for s in ACTIVE},
    **{kind: {s: s for s in ACTIVE} for kind in _ANNOTATIONS},
}


class Case(Contract):
    """`CaseState` plus what the aggregate needs to check guards and filter access."""

    state: CaseState
    origin_category: DataCategory
    categories: frozenset[DataCategory]
    approved_draft_id: MessageId | None = None


def open_case(
    case_id: CaseId, data: dict[str, Any], at: datetime, simulated: bool = False
) -> tuple[Case, dict[str, Any]]:
    opened = parse(K.CASE_OPENED, data)
    assert isinstance(opened, OpenedData)
    origin = (
        DataCategory.ADHERENCE
        if opened.opened_by == "guardian_agent"
        else DataCategory.CONVERSATIONS
    )
    case = Case(
        state=CaseState(
            id=case_id,
            patient_id=opened.patient_id,
            status=S.OPEN,
            language=opened.language,
            simulated=simulated,
            version=1,
        ),
        origin_category=origin,
        categories=frozenset({origin}),
    )
    return case, opened.model_dump(mode="json")


def apply(
    case: Case, kind: CaseEventKind, data: dict[str, Any], at: datetime
) -> tuple[Case, dict[str, Any]]:
    """The next case after this event, and the event data as it must be stored."""
    if kind is K.CASE_OPENED:
        raise InvalidTransition("case already opened", kind=kind.value)
    state = case.state
    target = TRANSITIONS[kind].get(state.status)
    if target is None:
        raise InvalidTransition("event not allowed in this state", kind=kind.value)
    if kind is K.DOCTOR_APPROVAL_REQUESTED and data.get("due_at") is None:
        data = {**data, "due_at": (at + APPROVAL_TIMEOUT).isoformat()}
    parsed = parse(kind, data)
    changes = _changes(case, kind, parsed, at)
    changes["state"] = state.model_copy(
        update={**changes.get("state", {}), "status": target, "version": state.version + 1}
    )
    changes["categories"] = case.categories | {category_of(case, kind)}
    return case.model_copy(update=changes), parsed.model_dump(mode="json")


def replay(
    case_id: CaseId, events: Iterable[tuple[CaseEventKind, dict[str, Any], datetime]]
) -> Case:
    case: Case | None = None
    for kind, data, at in events:
        if case is None:
            if kind is not K.CASE_OPENED:
                raise InvalidTransition("the first event must be case_opened", kind=kind.value)
            case = open_case(case_id, data, at)[0]
        else:
            case = apply(case, kind, data, at)[0]
    if case is None:
        raise InvalidTransition("no events")
    return case


def category_of(case: Case, kind: CaseEventKind) -> DataCategory:
    """The data category a viewer needs to see this event (per-item access)."""
    if kind is K.APPOINTMENT_BOOKED:
        return DataCategory.APPOINTMENTS
    if kind is K.GUARDIAN_UPDATED:
        return DataCategory.ADHERENCE
    if kind in (K.CASE_OPENED, K.CASE_CLOSED):
        return case.origin_category
    return DataCategory.CONVERSATIONS


# ----- Effects of each event on the state -------------------------------------------------------


def _changes(case: Case, kind: CaseEventKind, data: Contract, at: datetime) -> dict[str, Any]:
    state = case.state
    match data:
        case UrgencyData():
            floor = max_urgency(data.floor, state.urgency_floor)
            return {"state": {"urgency": max_urgency(data.urgency, floor), "urgency_floor": floor}}
        case RedFlagData():
            hits = tuple(
                RedFlagHit(rule_id=r, urgency=Urgency.EMERGENCY, rules_version=data.rules_version)
                for r in data.rule_ids
            )
            return {
                "state": {
                    "urgency": Urgency.EMERGENCY,
                    "urgency_floor": Urgency.EMERGENCY,
                    "red_flags": state.red_flags + hits,
                }
            }
        case QuestionData():
            question = OpenQuestion(
                key=data.key, asked_by=data.asked_by, asked_at=at, for_role=data.for_role
            )
            others = tuple(q for q in state.open_questions if q.key != data.key)
            return {"state": {"open_questions": (*others, question)}}
        case CaseFact():
            return {
                "state": {
                    "facts": (*(f for f in state.facts if f.key != data.key), data),
                    "open_questions": tuple(q for q in state.open_questions if q.key != data.key),
                }
            }
        case RouteSuggestion():
            return {"state": {"suggested_route": data}}
        case AppointmentData():
            return {"state": {"appointments": (*state.appointments, data.appointment_id)}}
        case GuardianData():
            branch = GuardianBranchState(
                status=data.status, last_alert_level=data.last_alert_level, updated_at=at
            )
            return {"state": {"guardian_status": branch}}
        case BriefData():
            return {"state": {"brief_id": data.brief_id}}
        case ApprovalRequestData():
            if state.pending_approval is not None or case.approved_draft_id is not None:
                raise InvalidTransition("a draft is already with the doctor", kind=kind.value)
            pending = PendingApproval(
                draft_id=data.draft_id, practitioner_id=data.practitioner_id, due_at=data.due_at
            )
            return {"state": {"pending_approval": pending}}
        case DecisionData():
            _require_pending(state, data.draft_id, kind)
            approved = data.decision in (DoctorDecision.APPROVED, DoctorDecision.EDITED)
            return {
                "state": {"pending_approval": None},
                "approved_draft_id": data.draft_id if approved else None,
            }
        case DraftData():  # approval timed out
            _require_pending(state, data.draft_id, kind)
            return {"state": {"pending_approval": None}}
        case ReplyData():
            if case.approved_draft_id is None or case.approved_draft_id != data.draft_id:
                raise InvalidTransition("the doctor has not approved this draft", kind=kind.value)
            return {"approved_draft_id": None}
    return {}


def _require_pending(state: CaseState, draft_id: MessageId, kind: CaseEventKind) -> None:
    if state.pending_approval is None or state.pending_approval.draft_id != draft_id:
        raise InvalidTransition("no pending approval for this draft", kind=kind.value)
