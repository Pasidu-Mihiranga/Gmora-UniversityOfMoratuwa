"""Tool registry (AGT-40): modules register their tools here at startup; agents call them only
through the `ToolInvoker`, which enforces each agent's allowlist, risk tier and audit."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel

from suwapath.contracts.principal import Principal
from suwapath.contracts.tools import ToolSpec

ToolHandler = Callable[[Principal, BaseModel], Awaitable[BaseModel]]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def add(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name} registered twice")
        self._tools[spec.name] = RegisteredTool(spec, handler)

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]
