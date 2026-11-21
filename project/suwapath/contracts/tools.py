"""Tool contracts (AGT-40). Same shape as MCP tools: name, description, JSON Schema in and out."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import DataCategory, ToolRisk


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str  # "<module>.<verb>", e.g. "navigation.find_doctors"
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    risk: ToolRisk
    data_categories: frozenset[DataCategory] = field(default_factory=frozenset)
    mcp_exposed: bool = False  # EXT-11: only tools without patient data in phase A

    def __post_init__(self) -> None:
        module, _, verb = self.name.partition(".")
        if not module or not verb:
            raise ValueError(f"tool name must be '<module>.<verb>', got {self.name!r}")
        if self.mcp_exposed and self.data_categories:
            raise ValueError(f"{self.name}: tools touching patient data cannot be MCP exposed")

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def output_schema(self) -> dict[str, Any]:
        return self.output_model.model_json_schema()


class ToolCall(Contract):
    call_id: str
    name: str
    arguments: dict[str, Any]


class ToolResult(Contract):
    call_id: str
    ok: bool
    output: dict[str, Any] | None = None
    error_code: str | None = None  # SuwaPathError.code; never a message with PHI
