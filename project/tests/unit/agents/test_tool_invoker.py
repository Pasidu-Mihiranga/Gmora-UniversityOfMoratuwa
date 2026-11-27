"""AGT-41, AGT-42: the tool invoker's checks, in order, and the audit of every call."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import BaseModel

from suwapath.contracts.agents import AgentSpec
from suwapath.contracts.enums import AgentKind, DataCategory, PrincipalKind, Purpose, Role, ToolRisk
from suwapath.contracts.errors import NotFound
from suwapath.contracts.ids import PatientId, TraceId, UserId
from suwapath.contracts.ports.audit import AuditEntryDraft
from suwapath.contracts.principal import Principal
from suwapath.contracts.tools import ToolCall, ToolSpec
from suwapath.modules.agents.application.context import JobLimit, StepCounter
from suwapath.modules.agents.application.tools import ToolInvoker
from suwapath.platform.tools import ToolRegistry
from suwapath.platform.uow import UnitOfWork

PRINCIPAL = Principal(
    kind=PrincipalKind.AGENT,
    agent_kind=AgentKind.PATIENT_COMPANION,
    role=Role.PATIENT,
    acting_for=UserId(uuid4()),
    patient_scope=PatientId(uuid4()),
    purpose=Purpose.CARE_NAVIGATION,
    trace_id=TraceId("0" * 31 + "7"),
)


class Query(BaseModel):
    specialty: str


class Answer(BaseModel):
    doctors: int


class FakeUow:
    def __init__(self) -> None:
        self.entries: list[AuditEntryDraft] = []

    async def audit(self, entry: AuditEntryDraft) -> None:
        self.entries.append(entry)


seen: list[Principal] = []


async def find(principal: Principal, args: BaseModel) -> BaseModel:
    seen.append(principal)
    assert isinstance(args, Query)
    if args.specialty == "missing":
        raise NotFound()
    if args.specialty == "crash":
        raise RuntimeError("database password is hunter2")
    return Answer(doctors=3)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.add(
        ToolSpec("navigation.find_doctors", "Find doctors", Query, Answer, ToolRisk.READ), find
    )
    registry.add(
        ToolSpec("booking.confirm", "Confirm", Query, Answer, ToolRisk.WRITE_IRREVERSIBLE), find
    )
    registry.add(
        ToolSpec(
            "record.sexual_health",
            "Sensitive",
            Query,
            Answer,
            ToolRisk.READ,
            frozenset({DataCategory.SENSITIVE_SEXUAL_HEALTH}),
        ),
        find,
    )
    return registry


def _invoker(steps: int = 8) -> tuple[ToolInvoker, FakeUow]:
    spec = AgentSpec(
        kind=AgentKind.PATIENT_COMPANION,
        acts_for_role=Role.PATIENT,
        purposes=frozenset({Purpose.CARE_NAVIGATION}),
        data_scope=frozenset({DataCategory.APPOINTMENTS}),
        tools=frozenset({"navigation.find_doctors", "booking.confirm", "record.sexual_health"}),
        may_message=frozenset(),
        prompt_version="test",
    )
    uow = FakeUow()
    invoker = ToolInvoker(_registry(), spec, PRINCIPAL, cast(UnitOfWork, uow), StepCounter(steps))
    return invoker, uow


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(call_id="c1", name=name, arguments=arguments)


async def test_an_allowed_tool_runs_with_the_agents_principal() -> None:
    invoker, uow = _invoker()
    result = await invoker.call(_call("navigation.find_doctors", specialty="cardiology"))
    assert result.ok and result.output == {"doctors": 3}
    assert seen[-1] == PRINCIPAL
    assert [(e.action, e.outcome, e.resource_id) for e in uow.entries] == [
        ("tool.call", "allow", "navigation.find_doctors")
    ]


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("record.read_all", {"specialty": "x"}, "tool_not_allowed"),
        ("navigation.find_doctors", {"specialty": 5, "extra": True}, "validation_failed"),
        ("booking.confirm", {"specialty": "x"}, "needs_person_action"),
        ("record.sexual_health", {"specialty": "x"}, "outside_agent_scope"),
        ("navigation.find_doctors", {"specialty": "missing"}, "not_found"),
        ("navigation.find_doctors", {"specialty": "crash"}, "tool_error"),
    ],
)
async def test_refusals_are_codes_and_audited(
    name: str, arguments: dict[str, Any], code: str
) -> None:
    invoker, uow = _invoker()
    result = await invoker.call(_call(name, **arguments))
    assert not result.ok
    assert result.error_code == code
    assert result.output is None
    assert uow.entries[-1].outcome == "deny"
    assert "hunter2" not in repr(uow.entries)


async def test_unregistered_tools_are_not_offered() -> None:
    invoker, _ = _invoker()
    assert "record.read_all" not in invoker.available()
    assert invoker.available() == [
        "booking.confirm",
        "navigation.find_doctors",
        "record.sexual_health",
    ]


async def test_each_call_takes_a_step() -> None:
    invoker, _ = _invoker(steps=1)
    await invoker.call(_call("navigation.find_doctors", specialty="x"))
    with pytest.raises(JobLimit) as stop:
        await invoker.call(_call("navigation.find_doctors", specialty="x"))
    assert stop.value.code == "max_steps"
