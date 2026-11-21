"""`ops` module registration (ARC-3). Binds ports, routes, subscribers and tools."""

from __future__ import annotations

from suwapath.modules.ops.api.router import dev_router, router
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    container.routers.append(router)
    if container.settings.dev_tools_enabled:
        container.routers.append(dev_router)
