"""Real provider smoke tests via the gateway. Opt-in with SUWAPATH_LIVE=1 (a fraction of a cent).
Synthetic text only; never patient data."""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import Language, Purpose
from suwapath.platform.clock import SystemClock
from suwapath.platform.config import Settings
from suwapath.platform.gateway.factory import build_gateway
from suwapath.platform.gateway.types import ChatMessage, ChatRequest, ChoiceQuestion, NoulQuestion

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("SUWAPATH_LIVE") != "1", reason="set SUWAPATH_LIVE=1 to call real providers"
    ),
]


class Extract(BaseModel):
    symptoms_en: list[str]
    missed_medicine: bool


async def test_gemini_structured_sinhala_and_streaming() -> None:
    gateway = build_gateway(Settings(), clock=SystemClock())
    request = ChatRequest(
        purpose=Purpose.CARE_NAVIGATION,
        fast=True,
        response_schema=Extract,
        language=Language.SI,
        messages=[
            ChatMessage(
                role="system",
                content="Extract symptoms in English and whether medicine was missed.",
            )
        ],
        untrusted=[
            UntrustedText(value="මට ඔලුව කැරකෙනවා. ඊයේ රෑ බෙහෙත් බිව්වේ නෑ.", source="synthetic_test")
        ],
    )
    result = await gateway.generate(request)
    assert result.parsed is not None and result.parsed.missed_medicine is True
    pieces = [
        p
        async for p in gateway.stream(
            ChatRequest(
                purpose=Purpose.CARE_NAVIGATION,
                language=Language.SI,
                messages=[ChatMessage(role="user", content="Say hello in Sinhala in five words.")],
            )
        )
    ]
    assert "".join(pieces).strip()


async def test_jev_decision() -> None:
    gateway = build_gateway(Settings(), clock=SystemClock())
    result = await gateway.decide(
        ["Synthetic message: 'mild cough for two days, no fever'."],
        {
            "fever": NoulQuestion(instructions="The message says the person has a fever now."),
            "when": ChoiceQuestion(
                instructions="How soon should they see a doctor?",
                criteria={"today": "today", "within_3_days": "3 days", "within_2_weeks": "2 weeks"},
            ),
        },
        purpose=Purpose.VERIFICATION,
    )
    assert result.answers["fever"].probability < 0.5  # type: ignore[union-attr]
    assert result.usage.cost_usd > 0
