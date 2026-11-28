"""Critical tier: SEC-20 to SEC-24 building blocks. AAD swaps and tampering fail, nonces never
repeat, the KEK wraps DEKs, and the DEK cache forgets after 5 minutes."""

from __future__ import annotations

import base64
import os
from uuid import uuid4

import pytest

from suwapath.platform.crypto import (
    CryptoError,
    DekCache,
    LocalKeyProvider,
    aad,
    new_dek,
    open_sealed,
    seal,
)

MASTER = base64.b64encode(os.urandom(32)).decode()


def test_a_sealed_value_opens_only_in_its_own_place() -> None:
    dek, row, patient = new_dek(), uuid4(), uuid4()
    here = aad("record_conditions", "code", row, patient)
    sealed = seal(dek, b"Type 2 diabetes", here, "local-1")
    assert open_sealed(dek, sealed, here) == b"Type 2 diabetes"
    for elsewhere in (
        aad("record_conditions", "code", uuid4(), patient),  # another row
        aad("record_conditions", "note", row, patient),  # another column
        aad("record_allergies", "code", row, patient),  # another table
        aad("record_conditions", "code", row, uuid4()),  # another patient
    ):
        with pytest.raises(CryptoError):
            open_sealed(dek, sealed, elsewhere)


def test_tampering_and_the_wrong_key_fail() -> None:
    dek, place = new_dek(), aad("t", "c", uuid4(), uuid4())
    sealed = bytearray(seal(dek, b"secret", place, "local-1"))
    sealed[-1] ^= 1
    with pytest.raises(CryptoError):
        open_sealed(dek, bytes(sealed), place)
    with pytest.raises(CryptoError):
        open_sealed(new_dek(), seal(dek, b"secret", place, "local-1"), place)
    with pytest.raises(CryptoError):
        open_sealed(dek, b"\x09junk", place)


def test_nonces_never_repeat_and_the_format_is_versioned() -> None:
    dek, place = new_dek(), aad("t", "c", uuid4(), uuid4())
    sealed = [seal(dek, b"same text", place, "local-1") for _ in range(2000)]
    header = 2 + len(b"local-1")
    nonces = {s[header : header + 12] for s in sealed}
    assert len(nonces) == 2000
    assert len(set(sealed)) == 2000  # equal plaintexts never give equal ciphertexts
    assert all(s[0] == 1 and s[2:header] == b"local-1" for s in sealed)
    assert all(b"same text" not in s for s in sealed)


def test_the_kek_wraps_and_unwraps_deks() -> None:
    provider, dek = LocalKeyProvider(MASTER), new_dek()
    wrapped = provider.wrap(dek)
    assert dek not in wrapped and provider.unwrap(wrapped, "local-1") == dek
    with pytest.raises(CryptoError):
        provider.unwrap(wrapped, "local-2")
    with pytest.raises(CryptoError):
        LocalKeyProvider(base64.b64encode(os.urandom(32)).decode()).unwrap(wrapped, "local-1")


@pytest.mark.parametrize("bad", ["", "not base64!", base64.b64encode(b"short").decode()])
def test_a_bad_master_key_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        LocalKeyProvider(bad)


def test_the_dek_cache_forgets_after_five_minutes_and_stays_small() -> None:
    clock = [0.0]
    cache = DekCache(ttl=300, size=2, now=lambda: clock[0])
    a, b, c = uuid4(), uuid4(), uuid4()
    cache.put(a, b"A")
    clock[0] = 299
    assert cache.get(a) == b"A"
    clock[0] = 301
    assert cache.get(a) is None
    cache.put(a, b"A")
    cache.put(b, b"B")
    cache.put(c, b"C")  # over size: the least recently used goes
    assert cache.get(a) is None and cache.get(c) == b"C"
    cache.drop(c)
    assert cache.get(c) is None
