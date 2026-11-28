"""Agent contracts and the agent communication protocol (docs/specs/05-agents-and-verifier.md).

Agents never call each other or share memory. They exchange `AgentMessage`s whose intent fixes the
kind, the allowed sender and recipient, the body schema and the default deadline (AGT-10 to AGT-13).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from suwapath.contracts.cases import CaseEventDraft
from suwapath.contracts.common import Contract, UntrustedText
from suwapath.contracts.enums import (
    AgentKind,
    DataCategory,
    DoseSlot,
    Language,
    MessageKind,
    MessageStatus,
    Purpose,
    Role,
    Urgency,
)
from suwapath.contracts.ids import (
    CaseId,
    EventId,
    JobId,
    MessageId,
    PatientId,
    ResourceId,
    TraceId,
    UserId,
)
from suwapath.contracts.suggestions import MAX_SUGGESTIONS, Suggestion

MAX_HOPS = 6  # AGT-12
MAX_REVISIONS = 2  # VER-8


class AgentSpec(Contract):
    kind: AgentKind
    acts_for_role: Role | None
    purposes: frozenset[Purpose]
    data_scope: frozenset[DataCategory]
    tools: frozenset[str]
    may_message: frozenset[str]  # AgentKind or Role values
    max_steps: int = 8
    max_wall_seconds: int = 60
    prompt_version: str


class AgentJob(Contract):
    id: JobId
    agent_kind: AgentKind
    case_id: CaseId | None
    patient_id: PatientId | None
    trigger_event_id: EventId | None
    acting_for: UserId | None
    purpose: Purpose
    input: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0
    trace_id: TraceId


class WaitCondition(Contract):
    kind: Literal["reply", "doctor_decision", "timer", "patient_tap"]
    ref: str
    until: datetime | None = None


# ----- Intents ---------------------------------------------------------------------------------


class MessageIntent(StrEnum):
    CHECK_IN_WITH_PATIENT = "check_in_with_patient"
    CHECK_IN_RESULT = "check_in_result"
    CONFIRM_MISSING_FACT = "confirm_missing_fact"
    MISSING_FACT_ANSWER = "missing_fact_answer"
    GUARDIAN_ALERT = "guardian_alert"
    EMERGENCY_ALERT = "emergency_alert"
    DOCTOR_BRIEF = "doctor_brief"
    REPLY_DRAFT = "reply_draft"
    REVISION_REQUEST = "revision_request"


class CheckInWithPatient(BaseModel):
    reason: Literal["missed_doses", "alert_rule", "no_response"]
    slots_missed: list[DoseSlot] = Field(default_factory=list)
    missed_count: int = Field(ge=0)


class CheckInResult(BaseModel):
    answered: bool
    patient_ok: Literal["yes", "no", "unclear"]
    urgency: Urgency | None = None
    consented_summary_keys: list[str] = Field(default_factory=list)  # i18n keys, not free text


class ConfirmMissingFact(BaseModel):
    fact_key: str  # e.g. "home_glucose_reading"
    question_key: str  # i18n key of the question to ask
    why_key: str


class MissingFactAnswer(BaseModel):
    fact_key: str
    answered: bool
    value: str | float | None = None
    communication_id: ResourceId | None = None  # provenance: patient_reported


class GuardianAlert(BaseModel):
    level: Literal["info", "concern", "urgent"]
    facts: dict[str, Any] = Field(default_factory=dict)  # only consented categories (VER-2 scope)
    suggested_action_key: str


class EmergencyAlert(BaseModel):
    """EMERGENCY_ALERT_FIELDS only (SEC-10 rule 7)."""

    patient_first_name: str
    advice_key: Literal["urgent_help_advised"] = "urgent_help_advised"
    contact_hint_key: str


class DoctorBrief(BaseModel):
    brief_id: ResourceId


class ReplyDraft(BaseModel):
    text: str = Field(max_length=4000)
    language: Literal["si", "en"]
    based_on_notes_id: ResourceId


class RevisionRequest(BaseModel):
    failed_checks: list[str]
    guidance_codes: list[str] = Field(default_factory=list)


class IntentSpec(Contract):
    intent: MessageIntent
    kind: MessageKind
    senders: frozenset[str]
    recipients: frozenset[str]
    body: type[BaseModel]
    default_deadline: timedelta | None = None


def _spec(
    intent: MessageIntent,
    kind: MessageKind,
    senders: set[str],
    recipients: set[str],
    body: type[BaseModel],
    deadline: timedelta | None = None,
) -> IntentSpec:
    return IntentSpec(
        intent=intent,
        kind=kind,
        senders=frozenset(senders),
        recipients=frozenset(recipients),
        body=body,
        default_deadline=deadline,
    )


_PC, _GA, _DA, _VER = (
    AgentKind.PATIENT_COMPANION.value,
    AgentKind.GUARDIAN_AGENT.value,
    AgentKind.DOCTOR_ASSISTANT.value,
    AgentKind.VERIFIER.value,
)
_PERSON_AGENTS = {_PC, _GA, _DA}

INTENTS: dict[MessageIntent, IntentSpec] = {
    s.intent: s
    for s in (
        _spec(
            MessageIntent.CHECK_IN_WITH_PATIENT,
            MessageKind.REQUEST,
            {_GA},
            {_PC},
            CheckInWithPatient,
            timedelta(minutes=30),
        ),
        _spec(MessageIntent.CHECK_IN_RESULT, MessageKind.ANSWER, {_PC}, {_GA}, CheckInResult),
        _spec(
            MessageIntent.CONFIRM_MISSING_FACT,
            MessageKind.REQUEST,
            {_DA},
            {_PC},
            ConfirmMissingFact,
            timedelta(hours=2),
        ),
        _spec(
            MessageIntent.MISSING_FACT_ANSWER, MessageKind.ANSWER, {_PC}, {_DA}, MissingFactAnswer
        ),
        _spec(
            MessageIntent.GUARDIAN_ALERT,
            MessageKind.ALERT,
            {_GA},
            {Role.GUARDIAN.value},
            GuardianAlert,
        ),
        _spec(
            MessageIntent.EMERGENCY_ALERT,
            MessageKind.ALERT,
            {_PC},
            {Role.GUARDIAN.value},
            EmergencyAlert,
        ),
        _spec(
            MessageIntent.DOCTOR_BRIEF, MessageKind.BRIEF, {_DA}, {Role.DOCTOR.value}, DoctorBrief
        ),
        _spec(
            MessageIntent.REPLY_DRAFT, MessageKind.DRAFT, {_DA}, {Role.PATIENT.value}, ReplyDraft
        ),
        _spec(
            MessageIntent.REVISION_REQUEST,
            MessageKind.REVISION_REQUEST,
            {_VER},
            _PERSON_AGENTS,
            RevisionRequest,
        ),
    )
}


class AgentMessage(Contract):
    message_id: MessageId
    conversation_id: MessageId
    in_reply_to: MessageId | None = None
    case_id: CaseId
    kind: MessageKind
    intent: MessageIntent
    sender: AgentKind
    recipient: str  # AgentKind or Role value
    acting_for: UserId | None
    consent_scope: frozenset[DataCategory]
    body: dict[str, Any]
    quoted: tuple[UntrustedText, ...] = ()
    hop: int = Field(ge=0, le=MAX_HOPS)
    revision: int = Field(default=0, ge=0)
    deadline: datetime | None = None
    trace_id: TraceId
    status: MessageStatus = MessageStatus.DRAFTED
    simulated: bool = False

    @model_validator(mode="after")
    def _matches_intent(self) -> AgentMessage:
        spec = INTENTS[self.intent]
        if self.kind is not spec.kind:
            raise ValueError(f"{self.intent} must be kind {spec.kind}")
        if self.sender.value not in spec.senders:
            raise ValueError(f"{self.sender} may not send {self.intent}")
        if self.recipient not in spec.recipients:
            raise ValueError(f"{self.intent} may not go to {self.recipient}")
        if self.kind is MessageKind.REQUEST and self.deadline is None:
            raise ValueError("a request needs a deadline (AGT-11)")
        spec.body.model_validate(self.body)
        return self

    @property
    def needs_verification(self) -> bool:
        """AGT-10: every person agent acts for a different person, so every message they send
        (to another agent or to a person) crosses roles. Verifier revision requests go back to
        their author and are not verified again."""
        return self.sender is not AgentKind.VERIFIER


class AgentMessageDraft(Contract):
    """What an agent returns; the runtime fills IDs, hop, trace and deadline defaults."""

    intent: MessageIntent
    recipient: str
    body: dict[str, Any]
    consent_scope: frozenset[DataCategory] = frozenset()
    quoted: tuple[UntrustedText, ...] = ()
    in_reply_to: MessageId | None = None
    # Same logical message, same key: retries never send twice (AGT-12). Default comes from the job.
    dedupe_hint: str | None = None


class AgentResult(Contract):
    case_events: tuple[CaseEventDraft, ...] = ()
    messages: tuple[AgentMessageDraft, ...] = ()
    status: Literal["done", "waiting", "handed_to_human", "failed"]
    wait_for: WaitCondition | None = None
    reason: str | None = None  # machine-readable code for handed_to_human or failed


# ----- What the companion shows the patient (API-22) --------------------------------------------


class CompanionReply(Contract):
    """One companion message to the patient, stored as JSON in the case communication.

    `text` is complete and human readable on its own; `suggestions` are the chips shown under it.
    Replies come from reviewed templates, never from free model text (invariant 1).
    """

    kind: Literal["question", "clarify", "result", "emergency", "check_in", "info"]
    text: str = Field(min_length=1, max_length=2000)
    language: Language
    suggestions: tuple[Suggestion, ...] = Field(default=(), max_length=MAX_SUGGESTIONS)
