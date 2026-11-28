"""The patient key store behind `PatientCipher` (SEC-20 to SEC-25).

Each patient's DEK is created on first use, stored only wrapped by the KEK, cached unwrapped in
this process for at most 5 minutes, and destroyed by crypto-shredding: the wrapped key is
overwritten with random bytes and marked destroyed, so nothing can open the patient's values again
(backups keep the old wrapped key until they expire; spec SEC-25 states this limit).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from suwapath.contracts.errors import NotFound
from suwapath.contracts.ids import PatientId
from suwapath.modules.record.adapters.models import PatientKeyRow
from suwapath.platform.crypto import (
    CryptoError,
    DekCache,
    KeyProvider,
    aad,
    new_dek,
    open_sealed,
    seal,
)
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory


class SqlPatientCipher:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        provider: Callable[[], KeyProvider],
        cache: DekCache | None = None,
    ) -> None:
        self._uow = uow_factory
        self._provider = provider  # built on first use, so a missing master key fails then
        self._cache = cache or DekCache()

    async def encrypt(
        self, patient_id: PatientId, table: str, column: str, row_id: UUID, value: bytes
    ) -> bytes:
        dek = await self._dek(patient_id, create=True)
        return seal(dek, value, aad(table, column, row_id, patient_id), self._provider().kek_id)

    async def decrypt(
        self, patient_id: PatientId, table: str, column: str, row_id: UUID, sealed: bytes
    ) -> bytes:
        dek = await self._dek(patient_id, create=False)
        try:
            return open_sealed(dek, sealed, aad(table, column, row_id, patient_id))
        except CryptoError:
            raise NotFound() from None

    async def destroy_key(self, patient_id: PatientId) -> None:
        async with self._uow.join() as uow:
            await uow.session.execute(
                update(PatientKeyRow)
                .where(PatientKeyRow.patient_id == patient_id)
                .values(wrapped_dek=os.urandom(60), destroyed_at=uow.clock.now())
            )
        self._cache.drop(patient_id)

    async def _dek(self, patient_id: PatientId, *, create: bool) -> bytes:
        cached = self._cache.get(patient_id)
        if cached is not None:
            return cached
        async with self._uow.join() as uow:
            row = await _key(uow, patient_id)
            if row is None and create:
                provider = self._provider()
                await uow.session.execute(
                    insert(PatientKeyRow)
                    .values(
                        patient_id=patient_id,
                        wrapped_dek=provider.wrap(new_dek()),
                        kek_id=provider.kek_id,
                        created_at=uow.clock.now(),
                    )
                    .on_conflict_do_nothing(index_elements=["patient_id"])
                )
                row = await _key(uow, patient_id)  # a racing first use: the winner's key
            if row is None or row[2] is not None or row[0] is None:
                raise NotFound()  # no key yet (nothing sealed) or crypto-shredded
            wrapped, kek_id = row[0], row[1]
        try:
            dek = self._provider().unwrap(wrapped, kek_id)
        except CryptoError:
            raise NotFound() from None
        self._cache.put(patient_id, dek)
        return dek


async def _key(uow: UnitOfWork, patient_id: PatientId) -> tuple[bytes | None, str, object] | None:
    found = (
        await uow.session.execute(
            select(
                PatientKeyRow.wrapped_dek, PatientKeyRow.kek_id, PatientKeyRow.destroyed_at
            ).where(PatientKeyRow.patient_id == patient_id)
        )
    ).first()
    return (found[0], found[1], found[2]) if found else None
