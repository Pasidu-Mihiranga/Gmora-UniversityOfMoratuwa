"""Envelope encryption (SEC-20 to SEC-25).

- A KEK (from `KeyProvider`) wraps each patient's 256-bit DEK.
- Fields are sealed with AES-256-GCM, a fresh 96-bit random nonce per value, and associated data
  `"{table}.{column}:{row_id}:{patient_id}"`, so a ciphertext copied to another row, column or
  patient fails to open (SEC-22).
- Sealed format: `version(1) | kek_id_len(1) | kek_id | nonce(12) | ciphertext+tag`.
- Unwrapped DEKs live only in this process, at most 5 minutes, 1000 entries (SEC-24).
Nothing here logs a key, a nonce or a plaintext.
"""

from __future__ import annotations

import base64
import os
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 1
NONCE_BYTES = 12
DEK_BYTES = 32
DEK_TTL_SECONDS = 300.0
DEK_CACHE_SIZE = 1000


class CryptoError(Exception):
    """A value could not be opened: wrong key, wrong place (AAD) or tampered. No detail given."""


class KeyProvider(Protocol):
    kek_id: str

    def wrap(self, dek: bytes) -> bytes: ...

    def unwrap(self, wrapped: bytes, kek_id: str) -> bytes: ...


class LocalKeyProvider:
    """SEC-21: the KEK is `SUWAPATH_MASTER_KEY` (base64 of 32 bytes), AES-256-GCM."""

    def __init__(self, master_key_b64: str, kek_id: str = "local-1") -> None:
        try:
            key = base64.b64decode(master_key_b64, validate=True)
        except ValueError:
            raise ValueError("SUWAPATH_MASTER_KEY must be base64") from None
        if len(key) != 32:
            raise ValueError("SUWAPATH_MASTER_KEY must decode to 32 bytes")
        self._aead = AESGCM(key)
        self.kek_id = kek_id

    def wrap(self, dek: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, dek, self.kek_id.encode())

    def unwrap(self, wrapped: bytes, kek_id: str) -> bytes:
        if kek_id != self.kek_id:
            raise CryptoError()
        try:
            return self._aead.decrypt(wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], kek_id.encode())
        except InvalidTag:
            raise CryptoError() from None


def new_dek() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def aad(table: str, column: str, row_id: UUID, patient_id: UUID) -> bytes:
    return f"{table}.{column}:{row_id}:{patient_id}".encode()


def seal(dek: bytes, plaintext: bytes, associated: bytes, kek_id: str) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    kek = kek_id.encode()
    header = bytes([VERSION, len(kek)]) + kek
    return header + nonce + AESGCM(dek).encrypt(nonce, plaintext, associated)


def open_sealed(dek: bytes, sealed: bytes, associated: bytes) -> bytes:
    try:
        if sealed[0] != VERSION:
            raise CryptoError()
        start = 2 + sealed[1]
        nonce, body = sealed[start : start + NONCE_BYTES], sealed[start + NONCE_BYTES :]
        return AESGCM(dek).decrypt(nonce, body, associated)
    except (InvalidTag, IndexError):
        raise CryptoError() from None


class DekCache:
    """LRU with a time limit (SEC-24). `now` is monotonic seconds; injectable for tests."""

    def __init__(
        self,
        ttl: float = DEK_TTL_SECONDS,
        size: int = DEK_CACHE_SIZE,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl, self._size, self._now = ttl, size, now
        self._items: OrderedDict[UUID, tuple[float, bytes]] = OrderedDict()

    def get(self, patient_id: UUID) -> bytes | None:
        found = self._items.get(patient_id)
        if found is None:
            return None
        if self._now() - found[0] > self._ttl:
            del self._items[patient_id]
            return None
        self._items.move_to_end(patient_id)
        return found[1]

    def put(self, patient_id: UUID, dek: bytes) -> None:
        self._items[patient_id] = (self._now(), dek)
        self._items.move_to_end(patient_id)
        while len(self._items) > self._size:
            self._items.popitem(last=False)

    def drop(self, patient_id: UUID) -> None:
        self._items.pop(patient_id, None)
