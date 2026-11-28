"""The case aggregate as other modules see it (docs/specs/02-contracts-and-domain.md section 5).

The `cases` module owns the event log and the state machine. Other modules read `CaseState` and
change a case only by appending `CaseEventDraft`s through `CaseService` with an expected version.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import AgentKind, CaseStatus, Language, Specialty, Urgency
from suwapath.contracts.ids import (
    AppointmentId,
    CaseId,
    EventId,
    MessageId,
    PatientId,
    PractitionerId,
    ResourceId,
)


class CaseEventKind(StrEnum):
    CASE_OPENED = "case_opened"
    INTAKE_STARTED = "intake_started"
    PATIENT_MESSAGE = "patient_message"
    QUESTION_ASKED = "question_asked"
    FACT_RECORDED = "fact_recorded"
    URGENCY_ASSESSED = "urgency_assessed"
    RED_FLAG_RAISED = "red_flag_raised"
    ROUTE_SUGGESTED = "route_suggested"
    APPOINTMENT_BOOKED = "appointment_booked"
    GUARDIAN_UPDATED = "guardian_updated"
    BRIEF_UPDATED = "brief_updated"
    DOCTOR_APPROVAL_REQUESTED = "doctor_approval_requested"
    DOCTOR_DECISION_RECORDED = "doctor_decision_recorded"
    APPROVAL_TIMED_OUT = "approval_timed_out"
    REPLY_DELIVERED = "reply_delivered"
    HANDED_TO_HUMAN = "handed_to_human"
    CASE_CLOSED = "case_closed"


class RedFlagHit(Contract):
    rule_id: str
    urgency: Urgency
    rules_version: str


class CaseFact(Contract):
    """A structured finding with provenance. `key` is a navigation concept or a fact key."""

    key: str
    present: bool = True
    value: str | float | None = None
    provenance_ids: tuple[ResourceId, ...] = ()
    source: Literal["patient_reported", "record", "report", "imaging", "doctor"] = (
        "patient_reported"
    )


class OpenQuestion(Contract):
    key: str
    asked_by: AgentKind
    asked_at: datetime
    for_role: Literal["patient", "doctor"] = "patient"
    # How the question was asked, so the next turn can read the answer (navigation question).
    question_key: str | None = None
    answer_type: Literal["yes_no", "choice", "number", "duration"] | None = None
    choices: tuple[str, ...] = ()
    red_flag_screen: bool = False


class RouteSuggestion(Contract):
    urgency: Urgency
    primary_specialty: Specialty
    alternatives: tuple[Specialty, ...] = ()
    confidence: float = Field(ge=0, le=1)
    low_confidence: bool
    kb_version: str


class GuardianBranchState(Contract):
    status: Literal["idle", "checking_in", "alerted", "escalated"]
    last_alert_level: Literal["info", "concern", "urgent", "emergency"] | None = None
    updated_at: datetime


class PendingApproval(Contract):
    draft_id: MessageId
    practitioner_id: PractitionerId
    due_at: datetime


class BudgetUsage(Contract):
    cost_usd: float = 0.0
    model_calls: int = 0
    agent_seconds: float = 0.0


class CaseState(Contract):
    id: CaseId
    patient_id: PatientId
    status: CaseStatus
    urgency: Urgency | None = None
    urgency_floor: Urgency | None = None
    language: Language
    red_flags: tuple[RedFlagHit, ...] = ()
    facts: tuple[CaseFact, ...] = ()
    open_questions: tuple[OpenQuestion, ...] = ()
    suggested_route: RouteSuggestion | None = None
    appointments: tuple[AppointmentId, ...] = ()
    guardian_status: GuardianBranchState | None = None
    brief_id: ResourceId | None = None
    pending_approval: PendingApproval | None = None
    budget_used: BudgetUsage = BudgetUsage()
    simulated: bool = False
    version: int = Field(ge=0)


class CaseEventDraft(Contract):
    """What an agent or module asks the case to record. `cases` checks the transition (CON-10)."""

    kind: CaseEventKind
    data: dict[str, Any] = Field(default_factory=dict)
    actor: str  # agent kind, role or module
    simulated: bool = False


class CaseEvent(Contract):
    event_id: EventId
    case_id: CaseId
    version: int = Field(ge=1)
    kind: CaseEventKind
    data: dict[str, Any]
    actor: str
    at: datetime
    simulated: bool = False
