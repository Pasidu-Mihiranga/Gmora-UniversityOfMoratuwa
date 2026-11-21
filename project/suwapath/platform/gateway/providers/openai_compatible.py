"""Any OpenAI-compatible chat API: OpenAI, OpenRouter, Groq, Ollama (06 section 2)."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from suwapath.contracts.errors import ProviderUnavailable, ValidationFailed
from suwapath.platform.gateway.pricing import estimate_cost
from suwapath.platform.gateway.types import (
    Capabilities,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    Usage,
)

PRESETS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",
}


class OpenAICompatibleChatModel:
    def __init__(
        self,
        *,
        preset: str,
        api_key: str,
        model: str,
        transport: httpx.AsyncBaseTransport | None = None,
        capabilities: Capabilities | None = None,
    ) -> None:
        self.name = f"{preset}:{model}"
        self.preset = preset
        self.model = model
        self.capabilities = capabilities or Capabilities(vision=False, tool_calling=True)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=PRESETS[preset],
            headers=headers,
            transport=transport,
            timeout=httpx.Timeout(30.0),
        )

    def _body(
        self, request: ChatRequest, prompt: list[ChatMessage], stream: bool
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in prompt],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "stream": stream,
        }
        if request.response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_schema.__name__,
                    "schema": request.response_schema.model_json_schema(),
                },
            }
        if self.preset == "openrouter":
            body["provider"] = {"data_collection": "deny"}  # SEC-33: no retention or training
        return body

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable("provider unavailable", status=response.status_code)
        if response.status_code >= 400:
            raise ValidationFailed("provider rejected the request", status=response.status_code)

    async def generate(self, request: ChatRequest, prompt: list[ChatMessage]) -> ChatResponse:
        started = time.perf_counter()
        try:
            response = await self._client.post(
                "/chat/completions", json=self._body(request, prompt, False)
            )
        except httpx.TransportError as exc:
            raise ProviderUnavailable("provider unreachable") from exc
        self._raise_for(response)
        data = response.json()
        usage = data.get("usage") or {}
        tokens_in, tokens_out = (
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )
        return ChatResponse(
            text=data["choices"][0]["message"].get("content") or "",
            usage=Usage(
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cost_usd=estimate_cost(self.model, tokens_in, tokens_out),
            ),
            provider=self.preset,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream(self, request: ChatRequest, prompt: list[ChatMessage]) -> AsyncIterator[str]:
        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=self._body(request, prompt, True)
            ) as r:
                self._raise_for(r)
                async for line in r.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    delta = json.loads(line[6:])["choices"][0].get("delta", {}).get("content")
                    if delta:
                        yield delta
        except httpx.TransportError as exc:
            raise ProviderUnavailable("provider unreachable") from exc
