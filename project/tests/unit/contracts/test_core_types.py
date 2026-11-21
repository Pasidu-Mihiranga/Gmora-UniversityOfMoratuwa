from uuid import uuid4

import pytest
from pydantic import ValidationError

from suwapath.contracts.enums import (
    AgentKind,
    PrincipalKind,
    Purpose,
    Role,
    Urgency,
    max_urgency,
)
from suwapath.contracts.ids import TraceId, UserId
from suwapath.contracts.principal import Principal

TRACE = TraceId("0" * 32)


def test_urgency_is_ordered() -> None:
    assert Urgency.EMERGENCY.at_least(Urgency.URGENT_24H)
    assert Urgency.URGENT_24H.at_least(Urgency.SOON_72H)
    assert Urgency.SOON_72H.at_least(Urgency.ROUTINE)
    assert not Urgency.ROUTINE.at_least(Urgency.SOON_72H)


def test_max_urgency_never_lowers_and_ignores_none() -> None:
    assert max_urgency(Urgency.URGENT_24H, Urgency.ROUTINE) is Urgency.URGENT_24H
    assert max_urgency(None, Urgency.SOON_72H, None) is Urgency.SOON_72H
    assert max_urgency(None, None) is None
    assert max_urgency(Urgency.ROUTINE, Urgency.EMERGENCY, Urgency.SOON_72H) is Urgency.EMERGENCY


def test_user_principal_requires_user_and_role() -> None:
    with pytest.raises(ValidationError):
        Principal(kind=PrincipalKind.USER, purpose=Purpose.SELF_ACCESS, trace_id=TRACE)


def test_user_cannot_act_for_someone_else() -> None:
    with pytest.raises(ValidationError):
        Principal(
            kind=PrincipalKind.USER,
            user_id=UserId(uuid4()),
            role=Role.GUARDIAN,
            acting_for=UserId(uuid4()),
            purpose=Purpose.GUARDIAN_MONITORING,
            trace_id=TRACE,
        )


def test_person_agent_must_act_for_a_human() -> None:
    with pytest.raises(ValidationError):
        Principal(
            kind=PrincipalKind.AGENT,
            agent_kind=AgentKind.GUARDIAN_AGENT,
            purpose=Purpose.GUARDIAN_MONITORING,
            trace_id=TRACE,
        )


def test_agent_effective_user_is_the_human_it_acts_for() -> None:
    human = UserId(uuid4())
    principal = Principal(
        kind=PrincipalKind.AGENT,
        agent_kind=AgentKind.PATIENT_COMPANION,
        role=Role.PATIENT,
        acting_for=human,
        purpose=Purpose.CARE_NAVIGATION,
        trace_id=TRACE,
    )
    assert principal.effective_user == human


def test_system_principal_carries_no_identity() -> None:
    with pytest.raises(ValidationError):
        Principal(
            kind=PrincipalKind.SYSTEM,
            role=Role.SYSTEM_ADMIN,
            purpose=Purpose.ADMINISTRATION,
            trace_id=TRACE,
        )


def test_principal_is_immutable() -> None:
    principal = Principal(kind=PrincipalKind.SYSTEM, purpose=Purpose.ADMINISTRATION, trace_id=TRACE)
    with pytest.raises(ValidationError):
        principal.purpose = Purpose.TREATMENT  # type: ignore[misc]
