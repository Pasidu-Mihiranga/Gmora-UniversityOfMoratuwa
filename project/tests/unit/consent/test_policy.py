"""Critical tier: SEC-10 rules 1 to 7, deny by default, and the property "no principal sees a
category outside its grant" over many random principals, grants and requests."""

from __future__ import annotations

import random
from uuid import uuid4

import pytest

from suwapath.contracts.enums import (
    SENSITIVE_CATEGORIES,
    AgentKind,
    DataCategory,
    PrincipalKind,
    Purpose,
    Role,
)
from suwapath.contracts.ids import PatientId, TraceId, UserId
from suwapath.contracts.ports.consent import AccessRequest
from suwapath.contracts.principal import Principal
from suwapath.modules.consent.domain.policy import (
    EMERGENCY_ALERT_FIELDS,
    Facts,
    Grant,
    decide,
)

C = DataCategory
P = Purpose
PATIENT = PatientId(uuid4())
OTHER_PATIENT = PatientId(uuid4())
TRACE = TraceId("7" * 32)


def user(role: Role, purpose: Purpose, patient: PatientId | None = None) -> Principal:
    uid = UserId(uuid4())
    return Principal(
        kind=PrincipalKind.USER,
        user_id=uid,
        role=role,
        acting_for=uid,
        patient_scope=patient,
        purpose=purpose,
        trace_id=TRACE,
    )


def agent(kind: AgentKind, purpose: Purpose) -> Principal:
    return Principal(
        kind=PrincipalKind.AGENT,
        agent_kind=kind,
        acting_for=UserId(uuid4()),
        patient_scope=PATIENT,
        purpose=purpose,
        trace_id=TRACE,
    )


def ask(principal: Principal, category: DataCategory, action: str = "read") -> AccessRequest:
    return AccessRequest(
        principal=principal,
        action=action,  # type: ignore[arg-type]
        patient_id=PATIENT,
        category=category,
        resource_type="Test",
        purpose=principal.purpose,
    )


GRANT = Grant(categories=frozenset({C.MEDICATIONS, C.ADHERENCE, C.APPOINTMENTS}), emergency=True)


# ----- rule 6 and deny by default ----------------------------------------------------------------


def test_system_principals_read_no_clinical_data() -> None:
    system = Principal(kind=PrincipalKind.SYSTEM, purpose=P.ADMINISTRATION, trace_id=TRACE)
    for category in C:
        assert not decide(ask(system, category), Facts()).allowed


def test_unknown_roles_and_missing_facts_are_denied() -> None:
    guardian = user(Role.GUARDIAN, P.GUARDIAN_MONITORING)
    assert not decide(ask(guardian, C.MEDICATIONS), Facts(human_role=Role.GUARDIAN)).allowed


# ----- rule 1: admins ----------------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.SYSTEM_ADMIN, Role.HOSPITAL_ADMIN])
def test_admins_never_read_clinical_categories(role: Role) -> None:
    admin = user(role, P.ADMINISTRATION)
    clinical = set(C) - {C.APPOINTMENTS}
    for category in clinical:
        assert not decide(ask(admin, category), Facts(human_role=role)).allowed


def test_hospital_admins_read_appointment_metadata_only() -> None:
    admin = user(Role.HOSPITAL_ADMIN, P.ADMINISTRATION)
    decision = decide(ask(admin, C.APPOINTMENTS), Facts(human_role=Role.HOSPITAL_ADMIN))
    assert decision.allowed and decision.allowed_fields is not None
    assert "reason" not in decision.allowed_fields
    assert not decide(
        ask(admin, C.APPOINTMENTS, "write"), Facts(human_role=Role.HOSPITAL_ADMIN)
    ).allowed


# ----- rule 2: the patient -----------------------------------------------------------------------


def test_patients_read_their_whole_record_and_nobody_elses() -> None:
    me = user(Role.PATIENT, P.SELF_ACCESS, PATIENT)
    for category in C:
        assert decide(
            ask(me, category), Facts(human_role=Role.PATIENT, own_patient=PATIENT)
        ).allowed
    other = user(Role.PATIENT, P.SELF_ACCESS, OTHER_PATIENT)
    facts = Facts(human_role=Role.PATIENT, own_patient=OTHER_PATIENT)
    assert not decide(ask(other, C.MEDICATIONS), facts).allowed


def test_patients_write_only_adherence_and_uploads() -> None:
    me = user(Role.PATIENT, P.SELF_ACCESS, PATIENT)
    facts = Facts(human_role=Role.PATIENT, own_patient=PATIENT)
    allowed = {c for c in C if decide(ask(me, c, "write"), facts).allowed}
    assert allowed == {C.ADHERENCE, C.REPORTS, C.IMAGING}


# ----- rules 3 and 7: guardians ------------------------------------------------------------------


def test_guardians_read_only_their_granted_categories() -> None:
    son = user(Role.GUARDIAN, P.GUARDIAN_MONITORING)
    facts = Facts(human_role=Role.GUARDIAN, guardian_grant=GRANT)
    allowed = {c for c in C if decide(ask(son, c), facts).allowed}
    assert allowed == {C.MEDICATIONS, C.ADHERENCE, C.APPOINTMENTS}


def test_guardians_never_write() -> None:
    son = user(Role.GUARDIAN, P.GUARDIAN_MONITORING)
    facts = Facts(human_role=Role.GUARDIAN, guardian_grant=GRANT)
    assert not any(decide(ask(son, c, "write"), facts).allowed for c in C)


def test_guardians_need_a_guardian_purpose() -> None:
    son = user(Role.GUARDIAN, P.CARE_NAVIGATION)
    facts = Facts(human_role=Role.GUARDIAN, guardian_grant=GRANT)
    assert not decide(ask(son, C.MEDICATIONS), facts).allowed


def test_sensitive_categories_never_reach_a_guardian_even_if_granted() -> None:
    son = user(Role.GUARDIAN, P.GUARDIAN_MONITORING)
    everything = Grant(categories=frozenset(C), emergency=True)
    facts = Facts(human_role=Role.GUARDIAN, guardian_grant=everything)
    for category in SENSITIVE_CATEGORIES:
        assert not decide(ask(son, category), facts).allowed


def test_the_emergency_alert_carries_only_its_fields_and_can_be_switched_off() -> None:
    son = user(Role.GUARDIAN, P.EMERGENCY)
    on = decide(ask(son, C.DEMOGRAPHICS), Facts(human_role=Role.GUARDIAN, guardian_grant=GRANT))
    assert on.allowed and on.allowed_fields == EMERGENCY_ALERT_FIELDS
    off = Grant(categories=GRANT.categories, emergency=False)
    assert not decide(
        ask(son, C.DEMOGRAPHICS), Facts(human_role=Role.GUARDIAN, guardian_grant=off)
    ).allowed
    assert not decide(ask(son, C.DEMOGRAPHICS), Facts(human_role=Role.GUARDIAN)).allowed  # no link


# ----- rule 4: doctors ---------------------------------------------------------------------------


def test_doctors_read_only_while_treating() -> None:
    doctor = user(Role.DOCTOR, P.TREATMENT)
    assert decide(ask(doctor, C.CONDITIONS), Facts(human_role=Role.DOCTOR, treating=True)).allowed
    assert not decide(ask(doctor, C.CONDITIONS), Facts(human_role=Role.DOCTOR)).allowed


def test_sensitive_categories_are_hidden_from_doctors_unless_switched_on() -> None:
    doctor = user(Role.DOCTOR, P.TREATMENT)
    hidden = Facts(human_role=Role.DOCTOR, treating=True)
    shown = Facts(
        human_role=Role.DOCTOR,
        treating=True,
        doctor_sensitive=frozenset({C.SENSITIVE_SEXUAL_HEALTH}),
    )
    assert not decide(ask(doctor, C.SENSITIVE_SEXUAL_HEALTH), hidden).allowed
    assert decide(ask(doctor, C.SENSITIVE_SEXUAL_HEALTH), shown).allowed
    assert not decide(ask(doctor, C.SENSITIVE_MENTAL_HEALTH), shown).allowed


# ----- rule 5: agents ----------------------------------------------------------------------------


def test_an_agent_gets_the_intersection_of_its_human_and_its_scope() -> None:
    guardian_agent = agent(AgentKind.GUARDIAN_AGENT, P.GUARDIAN_MONITORING)
    facts = Facts(
        human_role=Role.GUARDIAN,
        guardian_grant=GRANT,
        agent_scope=frozenset({C.ADHERENCE, C.CONDITIONS}),
    )
    allowed = {c for c in C if decide(ask(guardian_agent, c), facts).allowed}
    assert allowed == {
        C.ADHERENCE
    }  # conditions: in scope, not granted; medications: granted, not in scope


def test_an_agent_without_a_known_scope_or_human_is_denied() -> None:
    companion = agent(AgentKind.PATIENT_COMPANION, P.CARE_NAVIGATION)
    assert not decide(
        ask(companion, C.MEDICATIONS), Facts(human_role=Role.PATIENT, own_patient=PATIENT)
    ).allowed
    verifier = Principal(
        kind=PrincipalKind.AGENT,
        agent_kind=AgentKind.VERIFIER,
        purpose=P.VERIFICATION,
        trace_id=TRACE,
    )
    assert not decide(ask(verifier, C.MEDICATIONS), Facts(agent_scope=frozenset(C))).allowed


# ----- the property: never more than the grant ---------------------------------------------------


def _expected(request: AccessRequest, facts: Facts) -> bool:
    """An independent restatement of the rules, used only as the oracle."""
    p = request.principal
    if p.kind is PrincipalKind.SYSTEM or (p.kind is PrincipalKind.AGENT and p.acting_for is None):
        return False
    role = p.role if p.kind is PrincipalKind.USER else facts.human_role
    if p.kind is PrincipalKind.AGENT and (
        facts.agent_scope is None or request.category not in facts.agent_scope
    ):
        return False
    c, write = request.category, request.action == "write"
    if role is Role.PATIENT:
        return facts.own_patient == request.patient_id and (
            not write or c in {C.ADHERENCE, C.REPORTS, C.IMAGING}
        )
    if role is Role.GUARDIAN:
        g = facts.guardian_grant
        if write or g is None or c in SENSITIVE_CATEGORIES:
            return False
        if request.purpose is P.EMERGENCY and c is C.DEMOGRAPHICS:
            return g.emergency
        return request.purpose in (P.GUARDIAN_MONITORING, P.EMERGENCY) and c in g.categories
    if role is Role.DOCTOR:
        if not facts.treating or request.purpose not in (P.TREATMENT, P.DOCTOR_BRIEF):
            return False
        return c not in SENSITIVE_CATEGORIES or c in facts.doctor_sensitive
    if role is Role.HOSPITAL_ADMIN:
        return c is C.APPOINTMENTS and not write and request.purpose is P.ADMINISTRATION
    return False


def test_no_principal_ever_gets_more_than_the_rules_allow() -> None:
    rng = random.Random(20261128)  # noqa: S311  (seeded and repeatable, not for security)
    categories = list(C)
    for _ in range(5000):
        kind = rng.choice([PrincipalKind.USER, PrincipalKind.AGENT, PrincipalKind.SYSTEM])
        role = rng.choice(list(Role))
        purpose = rng.choice(list(P))
        if kind is PrincipalKind.USER:
            principal = user(role, purpose, rng.choice([PATIENT, OTHER_PATIENT, None]))
        elif kind is PrincipalKind.AGENT:
            principal = agent(rng.choice(list(AgentKind)), purpose)
        else:
            principal = Principal(kind=PrincipalKind.SYSTEM, purpose=purpose, trace_id=TRACE)
        grant = (
            Grant(
                categories=frozenset(rng.sample(categories, rng.randint(0, 6))),
                emergency=rng.random() < 0.5,
            )
            if rng.random() < 0.6
            else None
        )
        facts = Facts(
            human_role=role if rng.random() < 0.9 else None,
            own_patient=rng.choice([PATIENT, OTHER_PATIENT, None]),
            guardian_grant=grant,
            treating=rng.random() < 0.5,
            doctor_sensitive=frozenset(rng.sample(sorted(SENSITIVE_CATEGORIES), rng.randint(0, 2))),
            agent_scope=frozenset(rng.sample(categories, rng.randint(0, 8)))
            if rng.random() < 0.8
            else None,
        )
        request = ask(principal, rng.choice(categories), rng.choice(["read", "write"]))
        decision = decide(request, facts)
        if decision.allowed:
            assert _expected(request, facts), (request, facts, decision)
        if decision.allowed_fields is not None:
            assert request.category in (C.DEMOGRAPHICS, C.APPOINTMENTS)
