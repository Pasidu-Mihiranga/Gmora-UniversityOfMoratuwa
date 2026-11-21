"""Jev through OpenRouter's Decisions API, built to the shape verified on 27 Nov 2026 (GW-2a)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from suwapath.contracts.errors import ProviderUnavailable, ValidationFailed
from suwapath.platform.gateway.types import (
    Answer,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionResult,
    NoulAnswer,
    Question,
    Usage,
)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"


class JevDecisionModel:
    def __init__(
        self, *, api_key: str, model: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.name = f"decisions:{model}"
        self.model = model
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=httpx.Timeout(15.0),
        )

    async def decide(self, state: list[str], questions: dict[str, Question]) -> DecisionResult:
        body = {
            "model": self.model,
            "state": state,
            "questions": {key: q.model_dump() for key, q in questions.items()},
        }
        started = time.perf_counter()
        try:
            response = await self._client.post(DECISIONS_URL, json=body)
        except httpx.TransportError as exc:
            raise ProviderUnavailable("decision provider unreachable") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable("decision provider unavailable", status=response.status_code)
        if response.status_code >= 400:
            raise ValidationFailed("decision request rejected", status=response.status_code)
        data = response.json()
        answers = {
            key: _answer(questions[key], raw)
            for key, raw in data.get("answers", {}).items()
            if key in questions
        }
        missing = set(questions) - set(answers)
        if missing:
            raise ValidationFailed("decision response missing answers", missing=sorted(missing))
        usage = data.get("usage") or {}
        return DecisionResult(
            answers=answers,
            usage=Usage(
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                cost_usd=float(usage.get("cost", 0.0)),
                cost_is_estimate=False,
            ),
            model=str(data.get("model", self.model)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def _answer(question: Question, raw: dict[str, Any]) -> Answer:
    if isinstance(question, ChoiceQuestion):
        choice = str(raw["choice"])
        if choice not in question.criteria:
            raise ValidationFailed("decision returned an unknown option")
        return ChoiceAnswer(
            choice=choice,
            probabilities={k: float(v) for k, v in raw.get("probabilities", {}).items()},
            confidence=float(raw.get("confidence", 0.0)),
        )
    return NoulAnswer(probability=float(raw["noul"]))
