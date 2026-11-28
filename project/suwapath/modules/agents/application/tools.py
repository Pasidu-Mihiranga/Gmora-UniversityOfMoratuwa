"""`ToolInvoker` (AGT-41): the only way an agent calls a tool.

Checks, in order: the tool is in the agent's `spec.tools`; it exists; the arguments validate; its
risk tier is allowed (`write_irreversible` always needs a person's action); its data categories are
inside the agent's scope. Then it calls the handler with the agent's principal, so the owning module
authorizes the call itself (AGT-42). Every call is audited and timed (AUD-1). Errors come back as
codes, never as messages, so no patient data reaches the model through an error.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ValidationError

from suwapath.contracts.agents import AgentSpec
from suwapath.contracts.enums import ToolRisk
from suwapath.contracts.errors import SuwaPathError
from suwapath.contracts.ids import TraceId
from suwapath.contracts.ports.audit import AuditEntryDraft
from suwapath.contracts.principal import Principal
from suwapath.contracts.tools import ToolCall, ToolResult
from suwapath.modules.agents.application.context import StepCounter
from suwapath.platform.tools import ToolRegistry
from suwapath.platform.uow import UnitOfWork


class ToolInvoker:
    def __init__(
        self,
        registry: ToolRegistry,
        spec: AgentSpec,
        principal: Principal,
        uow: UnitOfWork,
        steps: StepCounter,
    ) -> None:
        self._registry = registry
        self._spec = spec
        self._principal = principal
        self._uow = uow
        self._steps = steps

    def available(self) -> list[str]:
        """Tool names this agent may call that are registered (for prompts and tool lists)."""
        return sorted(n for n in self._spec.tools if self._registry.get(n) is not None)

    async def call(self, call: ToolCall) -> ToolResult:
        self._steps.take()
        started = time.perf_counter()
        result = await self._call(call)
        await self._audit(call, result, int((time.perf_counter() - started) * 1000))
        return result

    async def _call(self, call: ToolCall) -> ToolResult:
        if call.name not in self._spec.tools:
            return _refused(call, "tool_not_allowed")
        tool = self._registry.get(call.name)
        if tool is None:
            return _refused(call, "tool_unknown")
        try:
            arguments = tool.spec.input_model.model_validate(call.arguments)
        except ValidationError:
            return _refused(call, "validation_failed")
        if tool.spec.risk is ToolRisk.WRITE_IRREVERSIBLE:
            return _refused(call, "needs_person_action")
        if not tool.spec.data_categories <= self._spec.data_scope:
            return _refused(call, "outside_agent_scope")
        try:
            output = await tool.handler(self._principal, arguments)
        except SuwaPathError as exc:
            return _refused(call, exc.code)
        except Exception:  # a tool bug must not crash the agent or leak details
            return _refused(call, "tool_error")
        if not isinstance(output, tool.spec.output_model):
            try:
                output = tool.spec.output_model.model_validate(
                    output.model_dump() if isinstance(output, BaseModel) else output
                )
            except ValidationError:
                return _refused(call, "tool_output_invalid")
        return ToolResult(call_id=call.call_id, ok=True, output=output.model_dump(mode="json"))

    async def _audit(self, call: ToolCall, result: ToolResult, elapsed_ms: int) -> None:
        principal = self._principal
        await self._uow.audit(
            AuditEntryDraft(
                actor_kind=principal.kind,
                actor_id=None,
                agent_kind=principal.agent_kind,
                acting_for=principal.acting_for,
                patient_id=principal.patient_scope,
                action="tool.call",
                resource_type="Tool",
                resource_id=call.name[:80],
                purpose=principal.purpose,
                outcome="allow" if result.ok else "deny",
                reason=result.error_code,
                trace_id=TraceId(principal.trace_id),
                metadata={"elapsed_ms": elapsed_ms},
            )
        )


def _refused(call: ToolCall, code: str) -> ToolResult:
    return ToolResult(call_id=call.call_id, ok=False, error_code=code)
