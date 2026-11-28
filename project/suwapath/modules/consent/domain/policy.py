"""The access policy (SEC-10 rules 1 to 7): a pure decision over gathered facts. Deny by default.

The facts are looked up fresh for every request by the application layer (SEC-12); nothing here
caches. Rules, in order:
1. system and hospital admins never read clinical categories; hospital admins read appointment
   metadata (their organisation is filtered by booking);
2. a patient reads their whole record and writes only adherence and uploads;
3. a guardian reads only the categories in an active grant, for guardian monitoring or emergency;
   sensitive categories never reach a guardian, even if granted (agreed 28 Nov 2026);
4. a doctor reads only while treating; sensitive categories only if the patient switched them on
   for that doctor;
5. an agent gets the intersection of its human's access and its declared data scope;
6. system principals read no clinical data;
7. with the emergency purpose a linked guardian receives only the emergency alert fields, unless
   the patient switched that off.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from suwapath.contracts.enums import (
    SENSITIVE_CATEGORIES,
    DataCategory,
    PrincipalKind,
    Purpose,
    Role,
)
from suwapath.contracts.ids import PatientId
from suwapath.contracts.ports.consent import AccessDecision, AccessRequest

POLICY_VERSION = "sec10-v1"
C = DataCategory
PATIENT_WRITES = frozenset({C.ADHERENCE, C.REPORTS, C.IMAGING})  # dose taken, uploads
GUARDIAN_PURPOSES = frozenset({Purpose.GUARDIAN_MONITORING, Purpose.EMERGENCY})
DOCTOR_PURPOSES = frozenset({Purpose.TREATMENT, Purpose.DOCTOR_BRIEF})
EMERGENCY_ALERT_FIELDS = frozenset({"patient_first_name", "advice_key", "contact_hint_key"})
APPOINTMENT_METADATA = frozenset(
    {"id", "slot_id", "start", "status", "number", "practitioner_role_id"}
)


@dataclass(frozen=True)
class Grant:
    """A patient's active grant to one guardian."""

    categories: frozenset[DataCategory]
    emergency: bool = True  # SEC-10 rule 7: on by default, the patient can switch it off


@dataclass(frozen=True)
class Facts:
    """What the application layer looked up for this one request."""

    human_role: Role | None = None  # the role of the human the principal is or acts for
    own_patient: PatientId | None = None  # the patient record a patient user owns
    guardian_grant: Grant | None = None  # the human's active grant from this patient
    treating: bool = False  # a care relationship with this patient is active now
    doctor_sensitive: frozenset[DataCategory] = field(default_factory=frozenset)
    agent_scope: frozenset[DataCategory] | None = None  # the agent's declared data scope


def decide(request: AccessRequest, facts: Facts) -> AccessDecision:
    allowed, reason, fields = _decide(request, facts)
    return AccessDecision(
        allowed=allowed,
        allowed_fields=fields if allowed else None,
        reason=reason,
        policy_version=POLICY_VERSION,
    )


def _decide(request: AccessRequest, facts: Facts) -> tuple[bool, str, frozenset[str] | None]:
    principal = request.principal
    if principal.kind is PrincipalKind.SYSTEM:
        return False, "system_no_clinical_access", None
    if principal.kind is PrincipalKind.AGENT:
        if principal.acting_for is None:
            return False, "agent_without_a_human", None
        if facts.agent_scope is None or request.category not in facts.agent_scope:
            return False, "outside_agent_scope", None
        role = facts.human_role
    else:
        role = principal.role
    if role is None:
        return False, "no_role", None
    return _human(role, request, facts)


def _human(
    role: Role, request: AccessRequest, facts: Facts
) -> tuple[bool, str, frozenset[str] | None]:
    category, write, purpose = request.category, request.action == "write", request.purpose
    if role is Role.SYSTEM_ADMIN:
        return False, "admin_no_clinical_access", None
    if role is Role.HOSPITAL_ADMIN:
        if category is C.APPOINTMENTS and not write and purpose is Purpose.ADMINISTRATION:
            return True, "appointment_metadata", APPOINTMENT_METADATA
        return False, "admin_no_clinical_access", None
    if role is Role.PATIENT:
        if facts.own_patient is None or facts.own_patient != request.patient_id:
            return False, "not_own_record", None
        if write and category not in PATIENT_WRITES:
            return False, "patient_write_not_allowed", None
        return True, "own_record", None
    if role is Role.GUARDIAN:
        grant = facts.guardian_grant
        if write:
            return False, "guardian_read_only", None
        if grant is None:
            return False, "guardian_grant_missing", None
        if category in SENSITIVE_CATEGORIES:
            return False, "sensitive_never_to_guardians", None
        if purpose is Purpose.EMERGENCY and category is C.DEMOGRAPHICS:
            if not grant.emergency:
                return False, "emergency_alert_switched_off", None
            return True, "emergency_alert", EMERGENCY_ALERT_FIELDS
        if purpose not in GUARDIAN_PURPOSES:
            return False, "purpose_not_allowed", None
        if category not in grant.categories:
            return False, "guardian_grant_missing", None
        return True, "guardian_grant", None
    if role is Role.DOCTOR:
        if not facts.treating:
            return False, "no_treating_relationship", None
        if purpose not in DOCTOR_PURPOSES:
            return False, "purpose_not_allowed", None
        if category in SENSITIVE_CATEGORIES and category not in facts.doctor_sensitive:
            return False, "sensitive_hidden", None
        return True, "treating", None
    return False, "unknown_role", None
