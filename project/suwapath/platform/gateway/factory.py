"""Build the gateway from settings (GW-3, GW-4)."""

from __future__ import annotations

import os

from suwapath.contracts.ports.ops import OpsService
from suwapath.platform.clock import Clock
from suwapath.platform.config import Settings
from suwapath.platform.gateway.gateway import ModelGateway
from suwapath.platform.gateway.providers.offline import OfflineChatModel, ScriptRegistry
from suwapath.platform.gateway.types import ChatModel, DecisionModel


def build_gateway(
    settings: Settings,
    *,
    clock: Clock,
    ops: OpsService | None = None,
    scripts: ScriptRegistry | None = None,
) -> ModelGateway:
    provider = settings.resolved_model_provider
    chat: ChatModel
    fast: ChatModel | None = None
    if provider == "gemini":
        from suwapath.platform.gateway.providers.gemini import GeminiChatModel

        if settings.google_application_credentials:
            os.environ.setdefault(
                "GOOGLE_APPLICATION_CREDENTIALS", settings.google_application_credentials
            )
        common = {
            "project": settings.google_cloud_project,
            "location": settings.google_cloud_location,
        }
        chat = GeminiChatModel(**common, model=settings.model_name or "gemini-3.5-flash")
        fast = GeminiChatModel(**common, model=settings.model_name_fast or "gemini-3.1-flash-lite")
    elif provider == "openai_compatible":
        from suwapath.platform.gateway.providers.openai_compatible import OpenAICompatibleChatModel

        key = settings.openai_compat_api_key.get_secret_value()
        chat = OpenAICompatibleChatModel(
            preset=settings.openai_compat_preset or "openai", api_key=key, model=settings.model_name
        )
        if settings.model_name_fast:
            fast = OpenAICompatibleChatModel(
                preset=settings.openai_compat_preset or "openai",
                api_key=key,
                model=settings.model_name_fast,
            )
    else:
        chat = OfflineChatModel(scripts)

    decision: DecisionModel | None = None
    if settings.resolved_decision_provider == "decisions":
        from suwapath.platform.gateway.providers.decisions import JevDecisionModel

        decision = JevDecisionModel(
            api_key=settings.openrouter_api_key.get_secret_value(), model=settings.jev_model
        )
        if decision.model == getattr(chat, "model", None):
            raise ValueError("the verifier must use a different model from the agents (GW-4)")
    return ModelGateway(chat, fast_chat=fast, decision=decision, ops=ops, clock=clock)
