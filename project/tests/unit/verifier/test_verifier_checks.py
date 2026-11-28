"""Stage 1 checks (VER-2): scope, wording, urgency, sources, canary and format, each passing a
safe message and failing the case it exists for. Wording uses the real safety patterns."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from suwapath.contracts.agents import AgentMessage, MessageIntent
from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import AgentKind, DataCategory, MessageKind, Role, Urgency
from suwapath.contracts.ids import CaseId, MessageId, TraceId, UserId
from suwapath.contracts.ports.consent import AccessDecision
from suwapath.contracts.ports.verifier import VerificationContext
from suwapath.modules.safety.application.service import RuleSafetyService
from suwapath.modules.verifier.domain.checks import (
    CanaryCheck,
    FormatCheck,
    ScopeCheck,
    SourcesCheck,
    UrgencyCheck,
    WordingCheck,
    default_checks,
)

ALLOWED = AccessDecision(allowed=True, reason="ok", policy_version="t")
SAFETY = RuleSafetyService.from_content()


def ctx(**extra: Any) -> VerificationContext:
    return VerificationContext(recipient_access=extra.pop("access", ALLOWED), **extra)


def message(
    intent: MessageIntent,
    body: dict[str, Any],
    *,
    sender: AgentKind,
    recipient: str,
    kind: MessageKind,
    scope: frozenset[DataCategory] = frozenset({DataCategory.ADHERENCE}),
    quoted: tuple[UntrustedText, ...] = (),
) -> AgentMessage:
    mid = MessageId(uuid4())
    return AgentMessage.model_construct(  # unvalidated, so a broken body can be checked too
        message_id=mid,
        conversation_id=mid,
        in_reply_to=None,
        case_id=CaseId(uuid4()),
        kind=kind,
        intent=intent,
        sender=sender,
        recipient=recipient,
        acting_for=UserId(uuid4()),
        consent_scope=scope,
        body=body,
        quoted=quoted,
        hop=1,
        revision=0,
        deadline=None,
        trace_id=TraceId("8" * 32),
    )


def alert(facts: dict[str, Any], **extra: Any) -> AgentMessage:
    return message(
        MessageIntent.GUARDIAN_ALERT,
        {"level": "concern", "facts": facts, "suggested_action_key": "alert.call_patient"},
        sender=AgentKind.GUARDIAN_AGENT,
        recipient=Role.GUARDIAN.value,
        kind=MessageKind.ALERT,
        **extra,
    )


def reply(text: str, language: str = "en") -> AgentMessage:
    return message(
        MessageIntent.REPLY_DRAFT,
        {"text": text, "language": language, "based_on_notes_id": str(uuid4())},
        sender=AgentKind.DOCTOR_ASSISTANT,
        recipient=Role.PATIENT.value,
        kind=MessageKind.DRAFT,
        scope=frozenset({DataCategory.CONDITIONS}),
    )


def answer(**body: Any) -> AgentMessage:
    return message(
        MessageIntent.MISSING_FACT_ANSWER,
        {"fact_key": "home_glucose_reading", **body},
        sender=AgentKind.PATIENT_COMPANION,
        recipient=AgentKind.DOCTOR_ASSISTANT.value,
        kind=MessageKind.ANSWER,
        scope=frozenset({DataCategory.OBSERVATIONS}),
    )


# ----- scope --------------------------------------------------------------------------------------


def test_scope_passes_consented_facts() -> None:
    result = ScopeCheck().run(alert({"missed_count": 2, "slots_missed": ["evening"]}), ctx())
    assert result.passed


def test_scope_blocks_when_the_recipient_may_not_see_it() -> None:
    denied = AccessDecision(allowed=False, reason="no_grant", policy_version="t")
    result = ScopeCheck().run(alert({"missed_count": 2}), ctx(access=denied))
    assert (result.passed, result.fixable, result.codes) == (False, False, ("no_grant",))
    assert not ScopeCheck().run(alert({}), VerificationContext(recipient_access=None)).passed


def test_scope_blocks_a_fact_outside_the_consent_scope_or_unknown() -> None:
    result = ScopeCheck().run(alert({"medication_names": ["metformin"], "diagnosis": "x"}), ctx())
    assert result.codes == ("unknown_fact:diagnosis", "fact_out_of_scope:medication_names")


# ----- wording ------------------------------------------------------------------------------------


def test_wording_passes_navigation_text() -> None:
    assert WordingCheck(SAFETY.check_wording).run(reply("Please see a doctor today."), ctx()).passed


def test_wording_returns_a_diagnosis_or_reassurance() -> None:
    check = WordingCheck(SAFETY.check_wording)
    assert check.run(reply("You have pneumonia."), ctx()).codes == ("EN-DX-001",)
    result = check.run(reply("ඔයාට කිසි ප්‍රශ්නයක් නැහැ.", "si"), ctx())
    assert (result.passed, result.fixable, result.codes) == (False, True, ("SI-RS-001",))


# ----- urgency ------------------------------------------------------------------------------------


def test_urgency_returns_text_that_says_to_wait_when_the_floor_is_urgent() -> None:
    message = reply("It can wait until next month.")
    assert UrgencyCheck().run(message, ctx(case_urgency_floor=Urgency.ROUTINE)).passed
    result = UrgencyCheck().run(message, ctx(case_urgency_floor=Urgency.URGENT_24H))
    assert result.codes == ("implies_waiting",)


def test_urgency_returns_a_stated_urgency_below_the_floor() -> None:
    result_msg = message(
        MessageIntent.CHECK_IN_RESULT,
        {"answered": True, "patient_ok": "no", "urgency": "routine"},
        sender=AgentKind.PATIENT_COMPANION,
        recipient=AgentKind.GUARDIAN_AGENT.value,
        kind=MessageKind.ANSWER,
    )
    result = UrgencyCheck().run(result_msg, ctx(case_urgency_floor=Urgency.URGENT_24H))
    assert result.codes == ("urgency_below_floor",)
    assert UrgencyCheck().run(result_msg, ctx()).passed  # no floor: nothing to compare


# ----- sources ------------------------------------------------------------------------------------


def test_sources_return_an_answer_without_its_provenance() -> None:
    assert not SourcesCheck().run(answer(answered=True, value=180), ctx()).passed
    assert (
        SourcesCheck()
        .run(answer(answered=True, value=180, communication_id=str(uuid4())), ctx())
        .passed
    )
    assert SourcesCheck().run(answer(answered=False, value=None), ctx()).passed


def test_sources_return_a_line_without_or_with_an_unknown_source() -> None:
    known = str(uuid4())
    body = {"lines": [{"text": "a", "source_ids": [known]}, {"text": "b", "source_ids": []}]}
    brief = message(
        MessageIntent.DOCTOR_BRIEF,
        body,
        sender=AgentKind.DOCTOR_ASSISTANT,
        recipient=Role.DOCTOR.value,
        kind=MessageKind.BRIEF,
    )
    assert SourcesCheck().run(brief, ctx()).codes == ("line_without_source:1",)
    body["lines"][1]["source_ids"] = [str(uuid4())]
    assert SourcesCheck().run(brief, ctx(source_texts={known: "x"})).codes == ("unknown_source:1",)


# ----- canary -------------------------------------------------------------------------------------


def test_canary_blocks_a_value_the_recipient_may_not_see() -> None:
    message = alert(
        {"missed_count": 2},
        quoted=(UntrustedText(value="I took METFORMIN", source="patient_message"),),
    )
    result = CanaryCheck().run(message, ctx(out_of_scope_values=frozenset({"metformin"})))
    assert (result.passed, result.fixable) == (False, False)
    assert CanaryCheck().run(message, ctx(out_of_scope_values=frozenset({"aspirin", " "}))).passed


# ----- format -------------------------------------------------------------------------------------


def test_format_returns_a_broken_body_too_long_text_or_the_wrong_language() -> None:
    assert FormatCheck().run(reply("Please see a doctor today."), ctx()).passed
    assert FormatCheck().run(reply("කරුණාකර අදම වෛද්‍යවරයෙකු හමුවන්න.", "si"), ctx()).passed
    broken = alert({"missed_count": 2})
    broken.body.pop("suggested_action_key")
    assert FormatCheck().run(broken, ctx()).codes == ("body_invalid",)
    assert "text_too_long" in FormatCheck().run(reply("a " * 2100), ctx()).codes
    assert FormatCheck().run(reply("Please come today.", "si"), ctx()).codes == ("wrong_language",)
    assert FormatCheck().run(reply("අද එන්න.", "en"), ctx()).codes == ("wrong_language",)


def test_all_six_checks_run() -> None:
    names = [c.name for c in default_checks(SAFETY.check_wording)]
    assert names == ["scope", "wording", "urgency", "sources", "canary", "format"]
