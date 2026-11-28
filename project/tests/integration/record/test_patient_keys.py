"""Critical tier: per-patient keys on Postgres (SEC-20, SEC-24, SEC-25)."""

from __future__ import annotations

import asyncio
import base64
import os
from uuid import uuid4

import pytest
from sqlalchemy import select

from suwapath.contracts.errors import NotFound
from suwapath.contracts.ids import PatientId
from suwapath.modules.record.adapters.models import PatientKeyRow
from suwapath.modules.record.application.keys import SqlPatientCipher
from suwapath.platform.crypto import DekCache, LocalKeyProvider
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
MASTER = base64.b64encode(os.urandom(32)).decode()


def cipher(uow_factory: UnitOfWorkFactory, master: str = MASTER) -> SqlPatientCipher:
    provider = LocalKeyProvider(master)
    return SqlPatientCipher(uow_factory, lambda: provider, DekCache())


async def test_values_survive_a_cold_cache_and_keys_are_stored_wrapped(
    uow_factory: UnitOfWorkFactory,
) -> None:
    patient, row = PatientId(uuid4()), uuid4()
    sealed = await cipher(uow_factory).encrypt(
        patient, "record_conditions", "code", row, b"Diabetes"
    )
    assert b"Diabetes" not in sealed
    assert (
        await cipher(uow_factory).decrypt(patient, "record_conditions", "code", row, sealed)
        == b"Diabetes"
    )
    async with uow_factory() as uow:
        stored = (
            await uow.session.execute(select(PatientKeyRow.wrapped_dek, PatientKeyRow.kek_id))
        ).one()
    assert len(stored[0]) > 32 and stored[1] == "local-1"


async def test_each_patient_has_their_own_key(uow_factory: UnitOfWorkFactory) -> None:
    c, row = cipher(uow_factory), uuid4()
    amma, other = PatientId(uuid4()), PatientId(uuid4())
    sealed = await c.encrypt(amma, "t", "c", row, b"x")
    await c.encrypt(other, "t", "c", uuid4(), b"y")
    with pytest.raises(NotFound):
        await c.decrypt(other, "t", "c", row, sealed)  # same place, other patient's key


async def test_a_racing_first_use_agrees_on_one_key(uow_factory: UnitOfWorkFactory) -> None:
    patient = PatientId(uuid4())
    rows = [uuid4() for _ in range(6)]
    ciphers = [cipher(uow_factory) for _ in rows]
    sealed = await asyncio.gather(
        *(c.encrypt(patient, "t", "c", r, b"v") for c, r in zip(ciphers, rows, strict=True))
    )
    fresh = cipher(uow_factory)
    assert [
        await fresh.decrypt(patient, "t", "c", r, s) for r, s in zip(rows, sealed, strict=True)
    ] == [b"v"] * 6
    async with uow_factory() as uow:
        assert len((await uow.session.execute(select(PatientKeyRow.patient_id))).all()) == 1


async def test_crypto_shredding_makes_everything_unreadable(uow_factory: UnitOfWorkFactory) -> None:
    c, patient, row = cipher(uow_factory), PatientId(uuid4()), uuid4()
    sealed = await c.encrypt(patient, "t", "c", row, b"secret")
    await c.destroy_key(patient)
    with pytest.raises(NotFound):
        await c.decrypt(patient, "t", "c", row, sealed)  # even this process's warm cache is dropped
    with pytest.raises(NotFound):
        await cipher(uow_factory).decrypt(patient, "t", "c", row, sealed)
    with pytest.raises(NotFound):
        await c.encrypt(patient, "t", "c", uuid4(), b"new")  # an erased patient gets no new data


async def test_another_master_key_cannot_open_the_data(uow_factory: UnitOfWorkFactory) -> None:
    patient, row = PatientId(uuid4()), uuid4()
    sealed = await cipher(uow_factory).encrypt(patient, "t", "c", row, b"secret")
    stranger = cipher(uow_factory, base64.b64encode(os.urandom(32)).decode())
    with pytest.raises(NotFound):
        await stranger.decrypt(patient, "t", "c", row, sealed)
