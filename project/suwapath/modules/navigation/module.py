"""`navigation` module registration (ARC-3): the navigation port over the versioned knowledge base
(loaded and checked once at startup, NAV-4) and the routes."""

from __future__ import annotations

from suwapath.modules.navigation.api.router import router
from suwapath.modules.navigation.application.service import KbNavigationService
from suwapath.modules.navigation.domain.kb import KnowledgeBase
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    ports = container.ports
    ports["navigation"] = KbNavigationService(KnowledgeBase.load(), lambda: ports)
    container.routers.append(router)
