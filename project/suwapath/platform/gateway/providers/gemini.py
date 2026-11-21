"""Gemini on Vertex AI through google-genai (GW-2b). Thinking is set to minimal where supported."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from suwapath.contracts.errors import ProviderUnavailable, ValidationFailed
from suwapath.platform.gateway.pricing import estimate_cost
from suwapath.platform.gateway.types import (
    Capabilities,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    Usage,
)

MINIMAL_THINKING = {"gemini-3.5-flash"}  # measured on 27 Nov 2026; 3.8-flash rejects minimal


class GeminiChatModel:
    capabilities = Capabilities(
        structured_output=True,
        tool_calling=True,
        vision=True,
        streaming=True,
        max_context_tokens=1_000_000,
    )

    def __init__(self, *, project: str, location: str, model: str) -> None:
        self.name = f"gemini:{model}"
        self.model = model
        self._client = genai.Client(vertexai=True, project=project, location=location)

    def _config(
        self, request: ChatRequest, prompt: list[ChatMessage]
    ) -> types.GenerateContentConfig:
        system = "\n\n".join(m.content for m in prompt if m.role == "system") or None
        config: dict[str, Any] = {
            "system_instruction": system,
            "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens,
        }
        if request.response_schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = request.response_schema
        if self.model in MINIMAL_THINKING:
            config["thinking_config"] = types.ThinkingConfig(thinking_level="minimal")
        return types.GenerateContentConfig(**config)

    @staticmethod
    def _contents(request: ChatRequest, prompt: list[ChatMessage]) -> list[types.Content]:
        contents = [
            types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[types.Part(text=m.content)],
            )
            for m in prompt
            if m.role != "system"
        ]
        if request.images and contents:
            contents[-1].parts = [
                *(contents[-1].parts or []),
                *(types.Part.from_bytes(data=img, mime_type="image/png") for img in request.images),
            ]
        return contents

    async def generate(self, request: ChatRequest, prompt: list[ChatMessage]) -> ChatResponse:
        started = time.perf_counter()
        try:
            result = await self._client.aio.models.generate_content(
                model=self.model,
                contents=self._contents(request, prompt),
                config=self._config(request, prompt),
            )
        except genai_errors.ServerError as exc:
            raise ProviderUnavailable("gemini server error", status=exc.code) from exc
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise ProviderUnavailable("gemini rate limited", status=429) from exc
            raise ValidationFailed("gemini rejected the request", status=exc.code) from exc
        latency = int((time.perf_counter() - started) * 1000)
        meta = result.usage_metadata
        tokens_in = (meta.prompt_token_count or 0) if meta else 0
        tokens_out = (meta.candidates_token_count or 0) if meta else 0
        return ChatResponse(
            text=result.text or "",
            parsed=result.parsed if request.response_schema else None,
            usage=Usage(
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cost_usd=estimate_cost(self.model, tokens_in, tokens_out),
            ),
            provider="gemini",
            model=self.model,
            latency_ms=latency,
        )

    async def stream(self, request: ChatRequest, prompt: list[ChatMessage]) -> AsyncIterator[str]:
        try:
            chunks = await self._client.aio.models.generate_content_stream(
                model=self.model,
                contents=self._contents(request, prompt),
                config=self._config(request, prompt),
            )
            async for chunk in chunks:
                if chunk.text:
                    yield chunk.text
        except genai_errors.APIError as exc:
            raise ProviderUnavailable("gemini stream failed", status=exc.code) from exc
