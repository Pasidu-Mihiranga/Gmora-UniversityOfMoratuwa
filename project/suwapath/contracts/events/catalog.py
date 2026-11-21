"""Event catalog v1 (docs/specs/03-events-and-processes.md section 3).

Adding or changing an event is a `contract-change` pull request that updates spec 03 and this file.
Payloads hold IDs, enums and numbers only (EVT-3).
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal

from suwapath.contracts.enums import (
    AgentKind,
    DataCategory,
    DecidedBy,
    DoctorDecision,
    DocumentStatus,
    DoseSlot,
    ImagingBand,
    MessageKind,
    Role,
    Urgency,
    ValidationStatus,
    VerdictKind,
)
from suwapath.contracts.events.envelope import EventPayload, register_payload
from suwapath.contracts.ids import (
    AppointmentId,
    CaseId,
    DocumentId,
    HoldId,
    MessageId,
    OfferingId,
    PatientId,
    PractitionerId,
    PractitionerRoleId,
    ReferralId,
    ResourceId,
    SlotId,
    TimerId,
    UserId,
)

# ----- identity, consent ----------------------------------------------------------------------


@register_payload
class UserRegistered(EventPayload):
    event_type: ClassVar[str] = "identity.user_registered"
    producer: ClassVar[str] = "identity"
    user_id: UserId
    role: Role


@register_payload
class GrantChanged(EventPayload):
    event_type: ClassVar[str] = "consent.grant_changed"
    producer: ClassVar[str] = "consent"
    patient_id: PatientId
    grantee_id: UserId
    categories: frozenset[DataCategory]
    change: Literal["granted", "revoked", "narrowed", "widened"]


@register_payload
class RuleConfirmed(EventPayload):
    event_type: ClassVar[str] = "consent.rule_confirmed"
    producer: ClassVar[str] = "consent"
    rule_id: ResourceId
    owner_id: UserId
    version: int


@register_payload
class SettingChanged(EventPayload):
    event_type: ClassVar[str] = "consent.setting_changed"
    producer: ClassVar[str] = "consent"
    owner_id: UserId
    setting_key: str
    version: int
    affects_user_ids: frozenset[UserId] = frozenset()


@register_payload
class SuggestionCreated(EventPayload):
    event_type: ClassVar[str] = "consent.suggestion_created"
    producer: ClassVar[str] = "consent"
    suggestion_id: ResourceId
    patient_id: PatientId
    guardian_id: UserId
    setting_key: str


# ----- record, reports, imaging ---------------------------------------------------------------


@register_payload
class ResourceWritten(EventPayload):
    event_type: ClassVar[str] = "record.resource_written"
    producer: ClassVar[str] = "record"
    resource_type: str
    resource_id: ResourceId
    patient_id: PatientId


@register_payload
class ReportUploaded(EventPayload):
    event_type: ClassVar[str] = "reports.report_uploaded"
    producer: ClassVar[str] = "reports"
    document_id: DocumentId
    patient_id: PatientId


@register_payload
class ReportExtracted(EventPayload):
    event_type: ClassVar[str] = "reports.report_extracted"
    producer: ClassVar[str] = "reports"
    report_id: ResourceId
    document_id: DocumentId
    patient_id: PatientId
    status: DocumentStatus
    observation_ids: tuple[ResourceId, ...] = ()


@register_payload
class StudyUploaded(EventPayload):
    event_type: ClassVar[str] = "imaging.study_uploaded"
    producer: ClassVar[str] = "imaging"
    study_id: ResourceId
    document_id: DocumentId
    patient_id: PatientId


@register_payload
class ScreeningCompleted(EventPayload):
    event_type: ClassVar[str] = "imaging.screening_completed"
    producer: ClassVar[str] = "imaging"
    study_id: ResourceId
    patient_id: PatientId
    band: ImagingBand
    flagged: bool
    model_version: str | None
    validation_status: ValidationStatus | None


# ----- care -----------------------------------------------------------------------------------


@register_payload
class DoseTaken(EventPayload):
    event_type: ClassVar[str] = "care.dose_taken"
    producer: ClassVar[str] = "care"
    administration_id: ResourceId
    statement_id: ResourceId
    patient_id: PatientId


@register_payload
class DoseMissed(EventPayload):
    event_type: ClassVar[str] = "care.dose_missed"
    producer: ClassVar[str] = "care"
    administration_id: ResourceId
    statement_id: ResourceId
    slot: DoseSlot | None
    scheduled_at: datetime
    patient_id: PatientId


@register_payload
class AlertRuleFired(EventPayload):
    event_type: ClassVar[str] = "care.alert_rule_fired"
    producer: ClassVar[str] = "care"
    rule_id: ResourceId
    rule_type: str
    patient_id: PatientId
    guardian_id: UserId | None


@register_payload
class Enrolled(EventPayload):
    event_type: ClassVar[str] = "care.enrolled"
    producer: ClassVar[str] = "care"
    enrollment_id: ResourceId
    patient_id: PatientId
    programme_key: str
    offering_id: OfferingId | None


@register_payload
class ReferralCreated(EventPayload):
    event_type: ClassVar[str] = "care.referral_created"
    producer: ClassVar[str] = "care"
    referral_id: ReferralId
    patient_id: PatientId
    programme_key: str
    referred_by: PractitionerId


# ----- booking --------------------------------------------------------------------------------


@register_payload
class SlotHeld(EventPayload):
    event_type: ClassVar[str] = "booking.slot_held"
    producer: ClassVar[str] = "booking"
    hold_id: HoldId
    slot_id: SlotId
    expires_at: datetime
    case_id: CaseId | None


@register_payload
class AppointmentBooked(EventPayload):
    event_type: ClassVar[str] = "booking.appointment_booked"
    producer: ClassVar[str] = "booking"
    appointment_id: AppointmentId
    practitioner_role_id: PractitionerRoleId
    patient_id: PatientId
    case_id: CaseId | None
    sensitive: bool = False


@register_payload
class AppointmentCancelled(EventPayload):
    event_type: ClassVar[str] = "booking.appointment_cancelled"
    producer: ClassVar[str] = "booking"
    appointment_id: AppointmentId
    by: Literal["patient", "hospital", "system"]
    patient_id: PatientId
    case_id: CaseId | None


# ----- cases ----------------------------------------------------------------------------------


@register_payload
class CaseOpened(EventPayload):
    event_type: ClassVar[str] = "cases.case_opened"
    producer: ClassVar[str] = "cases"
    case_id: CaseId
    patient_id: PatientId
    opened_by: Literal["patient", "guardian_agent", "system"]


@register_payload
class PatientMessageReceived(EventPayload):
    event_type: ClassVar[str] = "cases.patient_message_received"
    producer: ClassVar[str] = "cases"
    case_id: CaseId
    communication_id: ResourceId


@register_payload
class UrgencyAssessed(EventPayload):
    event_type: ClassVar[str] = "cases.urgency_assessed"
    producer: ClassVar[str] = "cases"
    case_id: CaseId
    urgency: Urgency
    floor: Urgency | None


@register_payload
class RedFlagRaised(EventPayload):
    event_type: ClassVar[str] = "cases.red_flag_raised"
    producer: ClassVar[str] = "cases"
    case_id: CaseId
    patient_id: PatientId
    rule_ids: tuple[str, ...]
    urgency: Urgency


@register_payload
class DoctorApprovalRequested(EventPayload):
    event_type: ClassVar[str] = "cases.doctor_approval_requested"
    producer: ClassVar[str] = "cases"
    case_id: CaseId
    draft_id: MessageId
    practitioner_id: PractitionerId
    due_at: datetime


@register_payload
class DoctorDecisionRecorded(EventPayload):
    event_type: ClassVar[str] = "cases.doctor_decision_recorded"
    producer: ClassVar[str] = "cases"
    case_id: CaseId
    draft_id: MessageId
    decision: DoctorDecision


@register_payload
class ReplyDelivered(EventPayload):
    event_type: ClassVar[str] = "cases.reply_delivered"
    producer: ClassVar[str] = "cases"
    case_id: CaseId
    communication_id: ResourceId


@register_payload
class HandedToHuman(EventPayload):
    event_type: ClassVar[str] = "cases.handed_to_human"
    producer: ClassVar[str] = "cases"
    case_id: CaseId
    reason: Literal[
        "budget", "verifier_unsure", "agent_failure", "revision_limit", "patient_request"
    ]


# ----- agents, verifier, notifications, ops, platform ----------------------------------------


@register_payload
class AgentMessageSent(EventPayload):
    event_type: ClassVar[str] = "agents.message_sent"
    producer: ClassVar[str] = "agents"
    message_id: MessageId
    kind: MessageKind
    from_agent: AgentKind
    to: str  # an AgentKind value or a Role value
    case_id: CaseId


@register_payload
class VerdictIssued(EventPayload):
    event_type: ClassVar[str] = "verifier.verdict_issued"
    producer: ClassVar[str] = "verifier"
    message_id: MessageId
    verdict: VerdictKind
    decided_by: DecidedBy
    case_id: CaseId | None


@register_payload
class NotificationDelivered(EventPayload):
    event_type: ClassVar[str] = "notifications.delivered"
    producer: ClassVar[str] = "notifications"
    notification_id: ResourceId
    channel: str


@register_payload
class BudgetExceeded(EventPayload):
    event_type: ClassVar[str] = "ops.budget_exceeded"
    producer: ClassVar[str] = "ops"
    scope: Literal["case", "agent", "daily", "user"]
    case_id: CaseId | None


@register_payload
class TimerFired(EventPayload):
    event_type: ClassVar[str] = "platform.timer_fired"
    producer: ClassVar[str] = "platform"
    timer_id: TimerId
    kind: str
    ref_type: str
    ref_id: str
