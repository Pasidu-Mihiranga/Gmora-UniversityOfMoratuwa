"""`consent` module registration (ARC-3): the access policy, grants and their routes."""

from __future__ import annotations

from typing import Any

from suwapath.modules.consent.api.router import build_router, stubs
from suwapath.modules.consent.application.service import SqlAccessPolicy, SqlConsentService
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    def identity() -> Any | None:
        return container.ports.get("identity")

    def agent_scopes() -> Any:
        return container.ports.get("agent_scopes", {})  # bound by the agents module

    container.ports["access_policy"] = SqlAccessPolicy(
        container.uow_factory, identity, agent_scopes
    )
    service = SqlConsentService(container.uow_factory, identity)
    container.ports["consent"] = service
    container.routers.extend([build_router(service), stubs])
