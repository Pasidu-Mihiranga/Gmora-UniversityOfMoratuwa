"""`safety` module registration (ARC-3): binds the safety port, loaded once from its content."""

from __future__ import annotations

from suwapath.modules.safety.api.router import router
from suwapath.modules.safety.application.service import RuleSafetyService
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    container.ports["safety"] = RuleSafetyService.from_content()
    container.routers.append(router)
