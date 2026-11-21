"""`agents` module registration (ARC-3). Binds ports, routes, subscribers and tools."""

from __future__ import annotations

from suwapath.modules.agents.api.router import router
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    container.routers.append(router)
