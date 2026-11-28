"""`record` module registration (ARC-3). Binds the patient cipher (M04) and the record routes."""

from __future__ import annotations

from functools import cache

from suwapath.modules.record.api.router import build_router
from suwapath.modules.record.application.keys import SqlPatientCipher
from suwapath.modules.record.application.service import SqlRecordService
from suwapath.platform.crypto import KeyProvider, LocalKeyProvider
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    @cache
    def provider() -> KeyProvider:
        master = container.settings.suwapath_master_key.get_secret_value()
        if not master:
            raise RuntimeError("SUWAPATH_MASTER_KEY is required to store patient data (SEC-21)")
        return LocalKeyProvider(master)

    cipher = SqlPatientCipher(container.uow_factory, provider)
    container.ports["patient_cipher"] = cipher
    service = SqlRecordService(
        container.uow_factory, lambda: container.ports.get("access_policy"), lambda: cipher
    )
    container.ports["record"] = service
    container.routers.append(build_router(service))
