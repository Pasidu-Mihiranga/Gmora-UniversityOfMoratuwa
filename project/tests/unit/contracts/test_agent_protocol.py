import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from suwapath.contracts.agents import INTENTS, AgentMessage, MessageIntent
from suwapath.contracts.enums import AgentKind, DataCategory, MessageKind, Role, ToolRisk
from suwapath.contracts.ids import CaseId, MessageId, TraceId, UserId
from suwapath.contracts.tools import ToolSpec

NOW = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)


def _message(**overrides: object) -> AgentMessage:
    mid = MessageId(uuid4())
    fields: dict[str, object] = {
        "message_id": mid,
        "conversation_id": mid,
        "case_id": CaseId(uuid4()),
        "kind": MessageKind.REQUEST,
        "intent": MessageIntent.CHECK_IN_WITH_PATIENT,
        "sender": AgentKind.GUARDIAN_AGENT,
        "recipient": AgentKind.PATIENT_COMPANION.value,
        "acting_for": UserId(uuid4()),
        "consent_scope": frozenset({DataCategory.ADHERENCE}),
        "body": {"reason": "missed_doses", "slots_missed": ["evening"], "missed_count": 2},
        "hop": 0,
        "deadline": NOW + timedelta(minutes=30),
        "trace_id": TraceId("c" * 32),
    }
    fields.update(overrides)
    return AgentMessage(**fields)  # type: ignore[arg-type]


def test_catalog_matches_spec_05_intents() -> None:
    """AGT-13: the code catalog and the spec 05 intent table name the same intents."""
    spec = (Path(__file__).parents[4] / "docs/specs/05-agents-and-verifier.md").read_text()
    table = spec.split("### Intent catalog (v1)")[1].split("`AGT-13`")[0]
    in_spec = set(re.findall(r"^\| `([a-z_]+)` \|", table, re.MULTILINE))
    assert set(INTENTS) == set(MessageIntent)
    assert {i.value for i in INTENTS} == in_spec


def test_valid_check_in_request() -> None:
    message = _message()
    assert message.needs_verification


def test_wrong_sender_is_rejected() -> None:
    with pytest.raises(ValidationError, match="may not send"):
        _message(sender=AgentKind.DOCTOR_ASSISTANT)


def test_wrong_recipient_is_rejected() -> None:
    with pytest.raises(ValidationError, match="may not go to"):
        _message(recipient=Role.GUARDIAN.value)


def test_request_without_deadline_is_rejected() -> None:
    with pytest.raises(ValidationError, match="deadline"):
        _message(deadline=None)


def test_body_must_match_the_intent_schema() -> None:
    with pytest.raises(ValidationError):
        _message(body={"reason": "because I said so", "missed_count": 2})


def test_hops_are_capped() -> None:
    with pytest.raises(ValidationError):
        _message(hop=7)


def test_verifier_revision_request_is_not_verified_again() -> None:
    message = _message(
        kind=MessageKind.REVISION_REQUEST,
        intent=MessageIntent.REVISION_REQUEST,
        sender=AgentKind.VERIFIER,
        recipient=AgentKind.DOCTOR_ASSISTANT.value,
        body={"failed_checks": ["sources"]},
        deadline=None,
    )
    assert not message.needs_verification


class _In(BaseModel):
    specialty: str


class _Out(BaseModel):
    ok: bool


def test_tool_spec_requires_namespaced_name() -> None:
    with pytest.raises(ValueError, match="<module>.<verb>"):
        ToolSpec("find_doctors", "d", _In, _Out, ToolRisk.READ)


def test_tool_with_patient_data_cannot_be_mcp_exposed() -> None:
    with pytest.raises(ValueError, match="cannot be MCP exposed"):
        ToolSpec(
            "record.read_summary",
            "d",
            _In,
            _Out,
            ToolRisk.READ,
            frozenset({DataCategory.MEDICATIONS}),
            mcp_exposed=True,
        )


def test_tool_schema_is_json_schema() -> None:
    spec = ToolSpec(
        "navigation.find_doctors", "Find doctors", _In, _Out, ToolRisk.READ, mcp_exposed=True
    )
    assert spec.input_schema()["properties"]["specialty"]["type"] == "string"
