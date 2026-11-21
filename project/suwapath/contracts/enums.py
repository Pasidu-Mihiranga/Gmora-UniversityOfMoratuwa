"""Shared enumerations (docs/specs/02-contracts-and-domain.md section 2)."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    PATIENT = "patient"
    GUARDIAN = "guardian"
    DOCTOR = "doctor"
    HOSPITAL_ADMIN = "hospital_admin"
    SYSTEM_ADMIN = "system_admin"


class PrincipalKind(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class AgentKind(StrEnum):
    PATIENT_COMPANION = "patient_companion"
    GUARDIAN_AGENT = "guardian_agent"
    DOCTOR_ASSISTANT = "doctor_assistant"
    VERIFIER = "verifier"
    SINGLE_AGENT_BASELINE = "single_agent_baseline"


class Purpose(StrEnum):
    CARE_NAVIGATION = "care_navigation"
    GUARDIAN_MONITORING = "guardian_monitoring"
    DOCTOR_BRIEF = "doctor_brief"
    BOOKING = "booking"
    VERIFICATION = "verification"
    EMERGENCY = "emergency"
    SELF_ACCESS = "self_access"
    TREATMENT = "treatment"
    ADMINISTRATION = "administration"
    EVALUATION = "evaluation"
    PRIVATE_CONSULT = "private_consult"


class DataCategory(StrEnum):
    DEMOGRAPHICS = "demographics"
    CONTACT = "contact"
    CONDITIONS = "conditions"
    ALLERGIES = "allergies"
    MEDICATIONS = "medications"
    ADHERENCE = "adherence"
    OBSERVATIONS = "observations"
    REPORTS = "reports"
    IMAGING = "imaging"
    APPOINTMENTS = "appointments"
    CONVERSATIONS = "conversations"
    SENSITIVE_SEXUAL_HEALTH = "sensitive_sexual_health"
    SENSITIVE_MENTAL_HEALTH = "sensitive_mental_health"


SENSITIVE_CATEGORIES: frozenset[DataCategory] = frozenset(
    {DataCategory.SENSITIVE_SEXUAL_HEALTH, DataCategory.SENSITIVE_MENTAL_HEALTH}
)


class Urgency(StrEnum):
    """Ordered: routine < soon_72h < urgent_24h < emergency (CON-5)."""

    ROUTINE = "routine"
    SOON_72H = "soon_72h"
    URGENT_24H = "urgent_24h"
    EMERGENCY = "emergency"

    @property
    def rank(self) -> int:
        return _URGENCY_ORDER[self]

    def at_least(self, other: Urgency) -> bool:
        return self.rank >= other.rank


_URGENCY_ORDER: dict[Urgency, int] = {
    Urgency.ROUTINE: 0,
    Urgency.SOON_72H: 1,
    Urgency.URGENT_24H: 2,
    Urgency.EMERGENCY: 3,
}


def max_urgency(*levels: Urgency | None) -> Urgency | None:
    """The only allowed way to combine urgencies (CON-5). `None` values are ignored."""
    present = [level for level in levels if level is not None]
    if not present:
        return None
    return max(present, key=lambda level: level.rank)


class Language(StrEnum):
    SI = "si"
    EN = "en"


class CaseStatus(StrEnum):
    OPEN = "open"
    INTAKE = "intake"
    ASSESSED = "assessed"
    EMERGENCY = "emergency"
    IN_CARE = "in_care"
    AWAITING_DOCTOR = "awaiting_doctor"
    REPLIED = "replied"
    HANDED_TO_HUMAN = "handed_to_human"
    CLOSED = "closed"


TERMINAL_CASE_STATUSES: frozenset[CaseStatus] = frozenset({CaseStatus.CLOSED})


class MessageKind(StrEnum):
    REQUEST = "request"
    ANSWER = "answer"
    ALERT = "alert"
    BRIEF = "brief"
    DRAFT = "draft"
    APPROVAL = "approval"
    BLOCK = "block"
    REVISION_REQUEST = "revision_request"
    INFO = "info"


class MessageStatus(StrEnum):
    DRAFTED = "drafted"
    VERIFYING = "verifying"
    DELIVERED = "delivered"
    RETURNED = "returned"
    BLOCKED = "blocked"
    ESCALATED = "escalated"
    HELD = "held"


class VerdictKind(StrEnum):
    ALLOW = "allow"
    REVISE = "revise"
    BLOCK = "block"
    ESCALATE = "escalate"
    HOLD = "hold"


class DecidedBy(StrEnum):
    RULES = "rules"
    MODEL = "model"
    HUMAN = "human"


class ValidationStatus(StrEnum):
    UNVALIDATED = "unvalidated"
    REPORTED = "reported"
    VALIDATED = "validated"


class ToolRisk(StrEnum):
    READ = "read"
    WRITE_REVERSIBLE = "write_reversible"
    WRITE_IRREVERSIBLE = "write_irreversible"


class Tier(StrEnum):
    """Cost and latency tiers (KNW-1)."""

    T0_RULES = "t0_rules"
    T1_CACHE = "t1_cache"
    T2_CLASSIFY = "t2_classify"
    T3_GENERATE = "t3_generate"


class Intent(StrEnum):
    """Intent catalog (KNW-3)."""

    EMERGENCY_OR_SYMPTOM = "emergency_or_symptom"
    GENERAL_HEALTH_QUESTION = "general_health_question"
    MY_RECORD_QUESTION = "my_record_question"
    REPORT_EXPLANATION = "report_explanation"
    BOOK_OR_FIND_DOCTOR = "book_or_find_doctor"
    MEDICINE_TAKEN_OR_SCHEDULE = "medicine_taken_or_schedule"
    SHARING_OR_SETTINGS = "sharing_or_settings"
    SMALLTALK_OR_OTHER = "smalltalk_or_other"
    UNSUPPORTED = "unsupported"


class Specialty(StrEnum):
    """Specialty catalog (NAV-3)."""

    GENERAL_MEDICINE = "general_medicine"
    CARDIOLOGY = "cardiology"
    ENDOCRINOLOGY = "endocrinology"
    NEPHROLOGY = "nephrology"
    NEUROLOGY = "neurology"
    RESPIRATORY = "respiratory"
    GASTROENTEROLOGY = "gastroenterology"
    ORTHOPAEDICS = "orthopaedics"
    RHEUMATOLOGY = "rheumatology"
    DERMATOLOGY = "dermatology"
    ENT = "ent"
    OPHTHALMOLOGY = "ophthalmology"
    PSYCHIATRY = "psychiatry"
    OBSTETRICS_GYNAECOLOGY = "obstetrics_gynaecology"
    PAEDIATRICS = "paediatrics"
    GENERAL_SURGERY = "general_surgery"
    UROLOGY = "urology"
    VENEREOLOGY = "venereology"


class DoseSlot(StrEnum):
    MORNING = "morning"
    NOON = "noon"
    EVENING = "evening"
    NIGHT = "night"


class AppointmentStatus(StrEnum):
    HELD = "held"
    BOOKED = "booked"
    CANCELLED = "cancelled"
    FULFILLED = "fulfilled"
    NOSHOW = "noshow"


class DocumentStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    NEEDS_MANUAL_ENTRY = "needs_manual_entry"
    PENDING_SCAN = "pending_scan"


class ImagingBand(StrEnum):
    """Doctor-facing screening bands (IMG-5)."""

    NO_CLEAR_SIGNS = "no_clear_signs"
    UNCERTAIN = "uncertain"
    POSSIBLE = "possible"
    SIGNS = "signs"
    UNAVAILABLE = "screening_unavailable"
    NOT_A_CHEST_XRAY = "not_a_chest_xray"


class DoctorDecision(StrEnum):
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"
