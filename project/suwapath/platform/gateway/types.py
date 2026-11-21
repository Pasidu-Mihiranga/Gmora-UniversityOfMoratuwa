"""Gateway request, response and provider protocols (GW-1 to GW-2b)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from suwapath.contracts.common import Contract, UntrustedText
from suwapath.contracts.enums import AgentKind, Language, Purpose
from suwapath.contracts.ids import CaseId


class Capabilities(Contract):
    structured_output: bool = True
    tool_calling: bool = False
    vision: bool = False
    streaming: bool = True
    languages: frozenset[Language] = frozenset({Language.SI, Language.EN})
    max_context_tokens: int = 128_000


class ChatMessage(Contract):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """Not frozen: the gateway attaches the minimized prompt text before calling the provider."""

    agent_kind: AgentKind | None = None
    purpose: Purpose
    case_id: CaseId | None = None
    step: str = "default"  # offline scripts are keyed by (agent_kind, purpose, step)
    messages: list[ChatMessage]
    untrusted: list[UntrustedText] = Field(default_factory=list)
    response_schema: type[BaseModel] | None = None
    images: list[bytes] = Field(default_factory=list)
    language: Language | None = None
    max_output_tokens: int = 800
    temperature: float = 0.2
    fast: bool = False  # use MODEL_NAME_FAST
    prompt_version: str | None = None


class Usage(Contract):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_is_estimate: bool = True


class ChatResponse(Contract):
    text: str
    parsed: Any = None
    usage: Usage
    provider: str
    model: str
    latency_ms: int
    ttft_ms: int | None = None
    simulated: bool = False


class ChatModel(Protocol):
    name: str
    capabilities: Capabilities

    async def generate(self, request: ChatRequest, prompt: list[ChatMessage]) -> ChatResponse: ...

    def stream(self, request: ChatRequest, prompt: list[ChatMessage]) -> AsyncIterator[str]: ...


# ----- Decision models (Jev) ---------------------------------------------------------------------


class NoulQuestion(Contract):
    type: Literal["noul"] = "noul"
    instructions: str


class ChoiceQuestion(Contract):
    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str]  # option key -> description (GW-2a)


Question = NoulQuestion | ChoiceQuestion


class NoulAnswer(Contract):
    type: Literal["noul"] = "noul"
    probability: float


class ChoiceAnswer(Contract):
    type: Literal["choice"] = "choice"
    choice: str
    probabilities: dict[str, float]
    confidence: float


Answer = NoulAnswer | ChoiceAnswer


class DecisionResult(Contract):
    answers: dict[str, Answer]
    usage: Usage
    model: str
    latency_ms: int


class DecisionModel(Protocol):
    name: str

    async def decide(self, state: list[str], questions: dict[str, Question]) -> DecisionResult: ...
