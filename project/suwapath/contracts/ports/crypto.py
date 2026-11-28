"""Per-patient field encryption (SEC-20 to SEC-25). Owned by `record` (it holds the patient keys);
used by every module that stores patient text: record, cases, agents, reports.

`table`, `column` and `row_id` name where the value lives; with the patient they form the
associated data, so a sealed value only opens in its own place (SEC-22).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from suwapath.contracts.ids import PatientId


class PatientCipher(Protocol):
    async def encrypt(
        self, patient_id: PatientId, table: str, column: str, row_id: UUID, value: bytes
    ) -> bytes:
        """Creates the patient's key on first use. Raises NotFound after crypto-shredding."""
        ...

    async def decrypt(
        self, patient_id: PatientId, table: str, column: str, row_id: UUID, sealed: bytes
    ) -> bytes:
        """Raises NotFound when the key was destroyed, or the value is not from this place."""
        ...

    async def destroy_key(self, patient_id: PatientId) -> None:
        """Crypto-shredding (SEC-25): every sealed value of the patient becomes unreadable."""
        ...
