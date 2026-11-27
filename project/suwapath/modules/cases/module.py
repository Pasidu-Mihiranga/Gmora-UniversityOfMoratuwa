"""`cases` module registration (ARC-3). Binds ports, routes, subscribers and tools."""

from __future__ import annotations

from suwapath.contracts.ports.consent import AccessPolicy
from suwapath.modules.cases.api.router import build_router, stream_stub
from suwapath.modules.cases.application.service import SqlCaseService
from suwapath.modules.cases.application.subscribers import CaseSubscribers
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    def policy() -> AccessPolicy | None:
        # Looked up per call: the consent module binds it; until then every read is denied.
        return container.ports.get("access_policy")

    service = SqlCaseService(container.uow_factory, policy)
    container.ports["cases"] = service
    CaseSubscribers(service, container.uow_factory).register(container.subscribers)
    container.routers.extend([build_router(service), stream_stub()])
