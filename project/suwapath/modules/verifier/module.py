"""`verifier` module registration (ARC-3): the verifier port, its check registry
(`verifier_checks`, spec 15) and the review routes.

Stage 2 uses Jev through the model gateway when a decision provider is configured (an
OpenRouter key); without one the verifier runs rules only (VER-10). The gateway is built on the
first model check, in whichever process verifies (the event worker).
"""

from __future__ import annotations

from typing import Any

from suwapath.contracts.enums import Purpose
from suwapath.modules.verifier.api.router import build_router
from suwapath.modules.verifier.application.service import DecideFn, SqlVerifier
from suwapath.modules.verifier.domain.checks import default_checks
from suwapath.platform.gateway.types import DecisionResult, Question
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    ports = container.ports
    settings = container.settings

    def wording(text: str, language: Any) -> Any:
        return ports["safety"].check_wording(text, language)

    ports["verifier_checks"] = default_checks(wording)
    built: dict[str, Any] = {}

    def decide_fn() -> DecideFn | None:
        if settings.resolved_decision_provider != "decisions":
            return None

        async def call(state: list[str], questions: dict[str, Question]) -> DecisionResult:
            if "gateway" not in built:
                from suwapath.platform.gateway.factory import build_gateway

                built["gateway"] = build_gateway(
                    settings, clock=container.clock, ops=ports.get("ops")
                )
            result: DecisionResult = await built["gateway"].decide(
                state, questions, purpose=Purpose.VERIFICATION
            )
            return result

        return call

    service = SqlVerifier(
        container.uow_factory, lambda: ports["verifier_checks"], lambda: ports, decide_fn
    )
    ports["verifier"] = service
    container.routers.append(build_router(service))
