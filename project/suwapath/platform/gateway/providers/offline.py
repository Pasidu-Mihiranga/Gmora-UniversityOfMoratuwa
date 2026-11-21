"""Offline mode (GW-14, ADR-0027): deterministic scripted replies so SuwaPath runs without keys.

Every response is marked `simulated=True`; the UI shows "Demo mode · sample replies". Offline mode
never stands in for imaging or for the verifier's model checks.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from suwapath.contracts.enums import AgentKind, Language, Purpose
from suwapath.contracts.errors import CapabilityMissing
from suwapath.platform.gateway.types import (
    Capabilities,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    Usage,
)

ScriptHandler = Callable[[ChatRequest], str | BaseModel]
ScriptKey = tuple[AgentKind | None, Purpose, str]

DEFAULT_REPLY = {
    Language.EN: "I can help you find the right care. Please tell me a little more.",
    Language.SI: "ඔබට සුදුසු සත්කාරය සොයා ගැනීමට මට උදව් කළ හැකියි. කරුණාකර තව ටිකක් කියන්න.",
}


@dataclass
class ScriptRegistry:
    handlers: dict[ScriptKey, ScriptHandler] = field(default_factory=dict)

    def register(
        self, agent_kind: AgentKind | None, purpose: Purpose, step: str, handler: ScriptHandler
    ) -> None:
        self.handlers[(agent_kind, purpose, step)] = handler

    def find(self, request: ChatRequest) -> ScriptHandler | None:
        return self.handlers.get(
            (request.agent_kind, request.purpose, request.step)
        ) or self.handlers.get((None, request.purpose, request.step))


class OfflineChatModel:
    name = "offline"
    capabilities = Capabilities(
        structured_output=True, tool_calling=False, vision=False, streaming=True
    )

    def __init__(self, scripts: ScriptRegistry | None = None) -> None:
        self.scripts = scripts or ScriptRegistry()

    def _run(self, request: ChatRequest) -> tuple[str, BaseModel | None]:
        handler = self.scripts.find(request)
        if handler is None:
            if request.response_schema is not None:
                raise CapabilityMissing(
                    f"no offline script for {request.agent_kind}/{request.purpose}/{request.step}"
                )
            return DEFAULT_REPLY[request.language or Language.EN], None
        result = handler(request)
        if isinstance(result, BaseModel):
            return result.model_dump_json(), result
        return result, None

    async def generate(self, request: ChatRequest, prompt: list[ChatMessage]) -> ChatResponse:
        started = time.perf_counter()
        text, parsed = self._run(request)
        return ChatResponse(
            text=text,
            parsed=parsed,
            usage=Usage(cost_is_estimate=False),
            provider="offline",
            model="offline-scripts",
            latency_ms=int((time.perf_counter() - started) * 1000),
            ttft_ms=0,
            simulated=True,
        )

    async def stream(self, request: ChatRequest, prompt: list[ChatMessage]) -> AsyncIterator[str]:
        text, _ = self._run(request)
        for word in text.split(" "):
            yield word + " "
