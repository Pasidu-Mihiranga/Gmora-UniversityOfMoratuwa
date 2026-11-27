"""Critical tier: the case state machine (docs/specs/02-contracts-and-domain.md section 5, CON-10).

Every row of the transition table, the guards, and the states each event is refused in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from suwapath.contracts.cases import CaseEventKind as K
from suwapath.contracts.enums import CaseStatus as S
from suwapath.contracts.enums import DataCategory, Urgency
from suwapath.contracts.errors import InvalidTransition, ValidationFailed
from suwapath.contracts.ids import CaseId
from suwapath.modules.cases.domain.aggregate import Case, apply, category_of, open_case, replay

AT = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
DRAFT = str(uuid4())
DOCTOR = str(uuid4())


def _opened(opened_by: str = "patient", reason: str | None = None) -> Case:
    return _open(opened_by, reason)[0]


def _open(opened_by: str = "patient", reason: str | None = None) -> tuple[Case, dict[str, Any]]:
    return open_case(
        CaseId(uuid4()),
        {"patient_id": str(uuid4()), "language": "si", "opened_by": opened_by, "reason": reason},
        AT,
    )


def _data(kind: K) -> dict[str, Any]:
    """Valid data for each kind, so table tests exercise only the transition."""
    return {
        K.CASE_OPENED: {"patient_id": str(uuid4()), "language": "en", "opened_by": "patient"},
        K.INTAKE_STARTED: {},
        K.PATIENT_MESSAGE: {"communication_id": str(uuid4())},
        K.QUESTION_ASKED: {"key": "fever_days", "asked_by": "patient_companion"},
        K.FACT_RECORDED: {"key": "fever_days", "value": 3},
        K.URGENCY_ASSESSED: {"urgency": "soon_72h", "floor": None},
        K.RED_FLAG_RAISED: {"rule_ids": ["RF-NEURO-001"]},
        K.ROUTE_SUGGESTED: {
            "urgency": "soon_72h",
            "primary_specialty": "general_medicine",
            "confidence": 0.7,
            "low_confidence": False,
            "kb_version": "kb-1",
        },
        K.APPOINTMENT_BOOKED: {"appointment_id": str(uuid4())},
        K.GUARDIAN_UPDATED: {"status": "alerted", "last_alert_level": "concern"},
        K.BRIEF_UPDATED: {"brief_id": str(uuid4())},
        K.DOCTOR_APPROVAL_REQUESTED: {
            "draft_id": DRAFT,
            "practitioner_id": DOCTOR,
            "due_at": (AT + timedelta(hours=4)).isoformat(),
        },
        K.DOCTOR_DECISION_RECORDED: {"draft_id": DRAFT, "decision": "approved"},
        K.APPROVAL_TIMED_OUT: {"draft_id": DRAFT},
        K.REPLY_DELIVERED: {"communication_id": str(uuid4()), "draft_id": DRAFT},
        K.HANDED_TO_HUMAN: {"reason": "agent_failure"},
        K.CASE_CLOSED: {},
    }[kind]


def _step(case: Case, kind: K, **override: Any) -> Case:
    data = _data(kind) | override
    return apply(case, kind, data, AT)[0]


# Paths that reach each status through allowed events only.
PATH_TO: dict[S, list[K]] = {
    S.OPEN: [],
    S.INTAKE: [K.INTAKE_STARTED],
    S.ASSESSED: [K.INTAKE_STARTED, K.URGENCY_ASSESSED],
    S.IN_CARE: [K.INTAKE_STARTED, K.URGENCY_ASSESSED, K.APPOINTMENT_BOOKED],
    S.AWAITING_DOCTOR: [
        K.INTAKE_STARTED,
        K.URGENCY_ASSESSED,
        K.APPOINTMENT_BOOKED,
        K.DOCTOR_APPROVAL_REQUESTED,
    ],
    S.REPLIED: [
        K.INTAKE_STARTED,
        K.URGENCY_ASSESSED,
        K.APPOINTMENT_BOOKED,
        K.DOCTOR_APPROVAL_REQUESTED,
        K.DOCTOR_DECISION_RECORDED,
        K.REPLY_DELIVERED,
    ],
    S.EMERGENCY: [K.RED_FLAG_RAISED],
    S.HANDED_TO_HUMAN: [K.HANDED_TO_HUMAN],
    S.CLOSED: [K.RED_FLAG_RAISED, K.CASE_CLOSED],
}


def _at(status: S) -> Case:
    case = _opened()
    for kind in PATH_TO[status]:
        case = _step(case, kind)
    assert case.state.status is status
    return case


ACTIVE = [s for s in S if s is not S.CLOSED]

# (from, event) -> to. Everything not listed here is refused (CON-10).
ALLOWED: dict[tuple[S, K], S] = {
    (S.OPEN, K.INTAKE_STARTED): S.INTAKE,
    (S.INTAKE, K.URGENCY_ASSESSED): S.ASSESSED,
    (S.ASSESSED, K.APPOINTMENT_BOOKED): S.IN_CARE,
    (S.IN_CARE, K.APPOINTMENT_BOOKED): S.IN_CARE,  # rebooking after an approval timeout
    (S.IN_CARE, K.DOCTOR_APPROVAL_REQUESTED): S.AWAITING_DOCTOR,
    (S.AWAITING_DOCTOR, K.DOCTOR_DECISION_RECORDED): S.AWAITING_DOCTOR,
    (S.AWAITING_DOCTOR, K.APPROVAL_TIMED_OUT): S.IN_CARE,
    **{(s, K.RED_FLAG_RAISED): S.EMERGENCY for s in ACTIVE},
    **{(s, K.HANDED_TO_HUMAN): S.HANDED_TO_HUMAN for s in ACTIVE if s is not S.HANDED_TO_HUMAN},
    **{(s, K.CASE_CLOSED): S.CLOSED for s in (S.REPLIED, S.EMERGENCY, S.HANDED_TO_HUMAN)},
    **{
        (s, k): s
        for s in ACTIVE
        for k in (
            K.PATIENT_MESSAGE,
            K.QUESTION_ASKED,
            K.FACT_RECORDED,
            K.GUARDIAN_UPDATED,
            K.BRIEF_UPDATED,
        )
    },
    **{(s, K.ROUTE_SUGGESTED): s for s in (S.INTAKE, S.ASSESSED, S.IN_CARE)},
}
# Reply delivery needs an approval first, so it is tested on its own below.
GUARDED = {K.REPLY_DELIVERED}


@pytest.mark.parametrize("status", list(S))
@pytest.mark.parametrize("kind", [k for k in K if k not in GUARDED])
def test_transition_table(status: S, kind: K) -> None:
    case = _at(status)
    expected = ALLOWED.get((status, kind))
    if expected is None:
        with pytest.raises(InvalidTransition):
            _step(case, kind)
        return
    after = _step(case, kind)
    assert after.state.status is expected
    assert after.state.version == case.state.version + 1


def test_open_starts_at_version_one() -> None:
    case = _opened()
    assert case.state.status is S.OPEN
    assert case.state.version == 1
    assert case.state.language.value == "si"


def test_a_second_case_opened_is_refused() -> None:
    with pytest.raises(InvalidTransition):
        _step(_opened(), K.CASE_OPENED)


def test_red_flag_sets_emergency_urgency_and_floor() -> None:
    case = _step(_at(S.ASSESSED), K.RED_FLAG_RAISED, rule_ids=["RF-CARD-002"])
    assert case.state.urgency is Urgency.EMERGENCY
    assert case.state.urgency_floor is Urgency.EMERGENCY
    assert [h.rule_id for h in case.state.red_flags] == ["RF-CARD-002"]


def test_red_flag_needs_a_rule() -> None:
    with pytest.raises(ValidationFailed):
        _step(_at(S.INTAKE), K.RED_FLAG_RAISED, rule_ids=[])


def test_a_second_red_flag_keeps_the_first() -> None:
    case = _step(_at(S.EMERGENCY), K.RED_FLAG_RAISED, rule_ids=["RF-RESP-003"])
    assert [h.rule_id for h in case.state.red_flags] == ["RF-NEURO-001", "RF-RESP-003"]


def test_emergency_refuses_booking_and_routes() -> None:
    """Invariant 3: a red flag stops booking."""
    case = _at(S.EMERGENCY)
    for kind in (K.APPOINTMENT_BOOKED, K.ROUTE_SUGGESTED, K.DOCTOR_APPROVAL_REQUESTED):
        with pytest.raises(InvalidTransition):
            _step(case, kind)


def test_assessment_never_goes_below_the_floor() -> None:
    """Invariant 2: a model may raise urgency, never lower it."""
    case = _step(_at(S.INTAKE), K.URGENCY_ASSESSED, urgency="routine", floor="urgent_24h")
    assert case.state.urgency is Urgency.URGENT_24H
    assert case.state.urgency_floor is Urgency.URGENT_24H


def test_assessment_may_raise_above_the_floor() -> None:
    case = _step(_at(S.INTAKE), K.URGENCY_ASSESSED, urgency="urgent_24h", floor="soon_72h")
    assert case.state.urgency is Urgency.URGENT_24H
    assert case.state.urgency_floor is Urgency.SOON_72H


@pytest.mark.parametrize("status", list(S))
def test_reply_is_refused_without_an_approved_draft(status: S) -> None:
    with pytest.raises(InvalidTransition):
        _step(_at(status), K.REPLY_DELIVERED)


def test_reply_needs_the_doctors_approval() -> None:
    """Invariant 6: no clinical reply without the doctor's approval."""
    with pytest.raises(InvalidTransition):
        _step(_at(S.AWAITING_DOCTOR), K.REPLY_DELIVERED)


@pytest.mark.parametrize("decision", ["approved", "edited"])
def test_approved_or_edited_reply_is_delivered(decision: str) -> None:
    case = _step(_at(S.AWAITING_DOCTOR), K.DOCTOR_DECISION_RECORDED, decision=decision)
    case = _step(case, K.REPLY_DELIVERED)
    assert case.state.status is S.REPLIED


def test_rejected_draft_cannot_be_delivered_but_can_be_revised() -> None:
    case = _step(_at(S.AWAITING_DOCTOR), K.DOCTOR_DECISION_RECORDED, decision="rejected")
    with pytest.raises(InvalidTransition):
        _step(case, K.REPLY_DELIVERED)
    revised = str(uuid4())
    case = _step(case, K.DOCTOR_APPROVAL_REQUESTED, draft_id=revised)
    assert case.state.pending_approval is not None
    assert str(case.state.pending_approval.draft_id) == revised


def test_reply_must_be_the_approved_draft() -> None:
    case = _step(_at(S.AWAITING_DOCTOR), K.DOCTOR_DECISION_RECORDED)
    with pytest.raises(InvalidTransition):
        _step(case, K.REPLY_DELIVERED, draft_id=str(uuid4()))


def test_decision_must_name_the_pending_draft() -> None:
    with pytest.raises(InvalidTransition):
        _step(_at(S.AWAITING_DOCTOR), K.DOCTOR_DECISION_RECORDED, draft_id=str(uuid4()))


def test_second_approval_request_while_one_is_pending_is_refused() -> None:
    with pytest.raises(InvalidTransition):
        _step(_at(S.AWAITING_DOCTOR), K.DOCTOR_APPROVAL_REQUESTED, draft_id=str(uuid4()))


def test_timeout_after_a_decision_is_refused() -> None:
    case = _step(_at(S.AWAITING_DOCTOR), K.DOCTOR_DECISION_RECORDED)
    with pytest.raises(InvalidTransition):
        _step(case, K.APPROVAL_TIMED_OUT)


def test_timeout_returns_to_care_and_clears_the_approval() -> None:
    case = _step(_at(S.AWAITING_DOCTOR), K.APPROVAL_TIMED_OUT)
    assert case.state.status is S.IN_CARE
    assert case.state.pending_approval is None


def test_facts_answer_open_questions() -> None:
    case = _step(_at(S.INTAKE), K.QUESTION_ASKED)
    assert [q.key for q in case.state.open_questions] == ["fever_days"]
    case = _step(case, K.FACT_RECORDED, value=3)
    case = _step(case, K.FACT_RECORDED, value=4)
    assert case.state.open_questions == ()
    assert [(f.key, f.value) for f in case.state.facts] == [("fever_days", 4)]


def test_unknown_data_fields_are_refused() -> None:
    with pytest.raises(ValidationFailed):
        _step(_at(S.OPEN), K.INTAKE_STARTED, note="free text")


def test_open_reason_must_be_a_code() -> None:
    with pytest.raises(ValidationFailed):
        _opened("guardian_agent", "Amma missed her tablets, call her")


def test_replay_rebuilds_the_same_state() -> None:
    case, opened = _open()
    events: list[tuple[K, dict[str, Any], datetime]] = [(K.CASE_OPENED, opened, AT)]
    for kind in PATH_TO[S.REPLIED]:
        case, data = apply(case, kind, _data(kind), AT)
        events.append((kind, data, AT))
    rebuilt = replay(case.state.id, events)
    assert rebuilt == case


def test_replay_needs_case_opened_first() -> None:
    with pytest.raises(InvalidTransition):
        replay(CaseId(uuid4()), [(K.INTAKE_STARTED, {}, AT)])


def test_categories_follow_the_events() -> None:
    patient_case = _step(_at(S.ASSESSED), K.APPOINTMENT_BOOKED)
    assert category_of(patient_case, K.CASE_OPENED) is DataCategory.CONVERSATIONS
    assert category_of(patient_case, K.APPOINTMENT_BOOKED) is DataCategory.APPOINTMENTS
    assert patient_case.categories == {DataCategory.CONVERSATIONS, DataCategory.APPOINTMENTS}

    dose_case = _opened("guardian_agent", "missed_doses")
    assert dose_case.categories == {DataCategory.ADHERENCE}
    dose_case = _step(dose_case, K.GUARDIAN_UPDATED)
    assert category_of(dose_case, K.GUARDIAN_UPDATED) is DataCategory.ADHERENCE
    assert category_of(dose_case, K.CASE_CLOSED) is DataCategory.ADHERENCE
