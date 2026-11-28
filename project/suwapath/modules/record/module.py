"""`record` module registration (ARC-3). Binds the patient cipher (M04) and the record routes."""

from __future__ import annotations

from functools import cache

from suwapath.modules.record.api.router import router
from suwapath.modules.record.application.keys import SqlPatientCipher
from suwapath.platform.crypto import KeyProvider, LocalKeyProvider
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    @cache
    def provider() -> KeyProvider:
        master = container.settings.suwapath_master_key.get_secret_value()
        if not master:
            raise RuntimeError("SUWAPATH_MASTER_KEY is required to store patient data (SEC-21)")
        return LocalKeyProvider(master)

    container.ports["patient_cipher"] = SqlPatientCipher(container.uow_factory, provider)
    container.routers.append(router)
