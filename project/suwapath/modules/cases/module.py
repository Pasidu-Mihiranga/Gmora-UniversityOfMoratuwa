"""`cases` module registration (ARC-3). Binds ports, routes, subscribers and tools."""

from __future__ import annotations

from suwapath.contracts.ports.consent import AccessPolicy
from suwapath.contracts.ports.crypto import PatientCipher
from suwapath.modules.cases.api.router import build_router
from suwapath.modules.cases.application.service import SqlCaseService
from suwapath.modules.cases.application.subscribers import CaseSubscribers
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    def policy() -> AccessPolicy | None:
        # Looked up per call: the consent module binds it; until then every read is denied.
        return container.ports.get("access_policy")

    def cipher() -> PatientCipher | None:
        return container.ports.get("patient_cipher")  # bound by record (M04)

    service = SqlCaseService(container.uow_factory, policy, cipher)
    container.ports["cases"] = service
    CaseSubscribers(service, container.uow_factory).register(container.subscribers)
    container.routers.append(build_router(service))
