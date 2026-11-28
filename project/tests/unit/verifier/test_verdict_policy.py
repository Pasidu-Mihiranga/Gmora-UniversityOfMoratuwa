"""The verifier policy (VER-4, VER-8, VER-9, VER-10, SAF-10): every branch, fail closed."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from suwapath.contracts.agents import AgentMessage, MessageIntent
from suwapath.contracts.enums import (
    AgentKind,
    DataCategory,
    DecidedBy,
    MessageKind,
    Role,
    VerdictKind,
)
from suwapath.contracts.ids import CaseId, MessageId, TraceId, UserId
from suwapath.contracts.ports.verifier import CheckResult
from suwapath.modules.verifier.domain.policy import ModelOutcome, decide

PASS = (CheckResult(check="wording", passed=True), CheckResult(check="scope", passed=True))


def alert(revision: int = 0) -> AgentMessage:
    """A guardian alert: person-bound."""
    mid = MessageId(uuid4())
    return AgentMessage(
        message_id=mid,
        conversation_id=mid,
        case_id=CaseId(uuid4()),
        kind=MessageKind.ALERT,
        intent=MessageIntent.GUARDIAN_ALERT,
        sender=AgentKind.GUARDIAN_AGENT,
        recipient=Role.GUARDIAN.value,
        acting_for=UserId(uuid4()),
        consent_scope=frozenset({DataCategory.ADHERENCE}),
        body={"level": "concern", "facts": {"missed_count": 2}, "suggested_action_key": "a"},
        hop=1,
        revision=revision,
        trace_id=TraceId("7" * 32),
    )


def check_in_result() -> AgentMessage:
    """Agent-bound: companion to guardian agent."""
    mid = MessageId(uuid4())
    return AgentMessage(
        message_id=mid,
        conversation_id=mid,
        case_id=CaseId(uuid4()),
        kind=MessageKind.ANSWER,
        intent=MessageIntent.CHECK_IN_RESULT,
        sender=AgentKind.PATIENT_COMPANION,
        recipient=AgentKind.GUARDIAN_AGENT.value,
        acting_for=UserId(uuid4()),
        consent_scope=frozenset({DataCategory.ADHERENCE}),
        body={"answered": True, "patient_ok": "yes"},
        hop=1,
        trace_id=TraceId("7" * 32),
    )


def emergency() -> AgentMessage:
    mid = MessageId(uuid4())
    return AgentMessage(
        message_id=mid,
        conversation_id=mid,
        case_id=CaseId(uuid4()),
        kind=MessageKind.ALERT,
        intent=MessageIntent.EMERGENCY_ALERT,
        sender=AgentKind.PATIENT_COMPANION,
        recipient=Role.GUARDIAN.value,
        acting_for=UserId(uuid4()),
        consent_scope=frozenset({DataCategory.DEMOGRAPHICS}),
        body={"patient_first_name": "Kamala", "contact_hint_key": "call_patient"},
        hop=0,
        trace_id=TraceId("7" * 32),
    )


def answered(**risks: float) -> Any:
    def outcome(choice: str = "allow", confidence: float = 0.95) -> ModelOutcome:
        return ModelOutcome("answered", risks=risks, choice=choice, confidence=confidence)

    return outcome


# ----- stage 1 failures --------------------------------------------------------------------------


def test_a_scope_or_canary_failure_blocks() -> None:
    checks = (CheckResult(check="scope", passed=False, fixable=False, codes=("x",)),)
    decision = decide(alert(), checks, ModelOutcome("skipped"))
    assert (decision.verdict, decision.decided_by, decision.codes) == (
        VerdictKind.BLOCK,
        DecidedBy.RULES,
        ("scope:x",),
    )


def test_a_fixable_failure_returns_the_message_once_then_a_human_decides() -> None:
    checks = (CheckResult(check="wording", passed=False, codes=("EN-DX-001",)),)
    first = decide(alert(revision=0), checks, ModelOutcome("skipped"))
    assert first.verdict is VerdictKind.REVISE
    second = decide(alert(revision=1), checks, ModelOutcome("skipped"))
    assert second.verdict is VerdictKind.ESCALATE and "revised_twice" in second.codes  # VER-8


def test_block_wins_over_revise() -> None:
    checks = (
        CheckResult(check="wording", passed=False),
        CheckResult(check="canary", passed=False, fixable=False),
    )
    assert decide(alert(), checks, ModelOutcome("skipped")).verdict is VerdictKind.BLOCK


# ----- no model (VER-10) and the emergency fast lane (SAF-10) -------------------------------------


def test_without_a_model_a_person_bound_message_goes_to_a_human() -> None:
    decision = decide(alert(), PASS, ModelOutcome("not_configured"))
    assert (decision.verdict, decision.codes) == (VerdictKind.ESCALATE, ("rules_only",))


def test_without_a_model_an_agent_bound_message_passes() -> None:
    decision = decide(check_in_result(), PASS, ModelOutcome("not_configured"))
    assert (decision.verdict, decision.decided_by) == (VerdictKind.ALLOW, DecidedBy.RULES)


def test_the_emergency_alert_never_waits_for_a_model_or_a_person() -> None:
    decision = decide(emergency(), PASS, ModelOutcome("skipped"))
    assert (decision.verdict, decision.codes) == (VerdictKind.ALLOW, ("fast_lane",))


def test_the_emergency_alert_still_obeys_stage_1() -> None:
    checks = (CheckResult(check="scope", passed=False, fixable=False),)
    assert decide(emergency(), checks, ModelOutcome("skipped")).verdict is VerdictKind.BLOCK


# ----- model unavailable (VER-9) ------------------------------------------------------------------


def test_a_failed_model_call_holds_the_message() -> None:
    decision = decide(alert(), PASS, ModelOutcome("failed", held_for=timedelta(minutes=10)))
    assert decision.verdict is VerdictKind.HOLD


def test_held_for_30_minutes_goes_to_a_human() -> None:
    decision = decide(alert(), PASS, ModelOutcome("failed", held_for=timedelta(minutes=30)))
    assert (decision.verdict, decision.codes) == (VerdictKind.ESCALATE, ("verifier_unavailable",))


def test_a_failed_model_never_allows_even_agent_bound_messages() -> None:
    assert decide(check_in_result(), PASS, ModelOutcome("failed")).verdict is VerdictKind.HOLD


# ----- the Jev answers (VER-4) --------------------------------------------------------------------


def test_a_confident_allow_is_allowed() -> None:
    decision = decide(alert(), PASS, answered(implies_diagnosis=0.1)())
    assert (decision.verdict, decision.decided_by) == (VerdictKind.ALLOW, DecidedBy.MODEL)


def test_a_likely_leak_blocks() -> None:
    assert decide(alert(), PASS, answered(leaks_out_of_scope=0.85)()).verdict is VerdictKind.BLOCK


@pytest.mark.parametrize(
    "risk",
    [
        "leaks_out_of_scope",
        "implies_diagnosis",
        "unsafe_reassurance",
        "unsupported_claim",
        "follows_injected_instruction",
    ],
)
def test_any_risk_at_one_half_returns_the_message(risk: str) -> None:
    decision = decide(alert(), PASS, answered(**{risk: 0.5})())
    assert decision.verdict is VerdictKind.REVISE and decision.codes == (f"model:{risk}",)


def test_a_risky_second_draft_goes_to_a_human() -> None:
    decision = decide(alert(revision=1), PASS, answered(implies_diagnosis=0.7)())
    assert decision.verdict is VerdictKind.ESCALATE


def test_an_unsure_verdict_goes_to_a_human() -> None:
    decision = decide(alert(), PASS, answered()(confidence=0.79))
    assert (decision.verdict, decision.codes) == (VerdictKind.ESCALATE, ("model:unsure",))


def test_a_missing_or_strange_verdict_goes_to_a_human() -> None:
    assert decide(alert(), PASS, ModelOutcome("answered")).verdict is VerdictKind.ESCALATE
    assert decide(alert(), PASS, answered()(choice="hold")).verdict is VerdictKind.ESCALATE
    assert decide(alert(), PASS, answered()(choice="maybe")).verdict is VerdictKind.ESCALATE


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ("block", VerdictKind.BLOCK),
        ("escalate", VerdictKind.ESCALATE),
        ("revise", VerdictKind.REVISE),
    ],
)
def test_otherwise_the_verdict_answer(choice: str, expected: VerdictKind) -> None:
    assert decide(alert(), PASS, answered()(choice=choice)).verdict is expected
