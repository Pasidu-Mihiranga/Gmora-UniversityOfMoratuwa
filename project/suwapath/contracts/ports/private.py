"""private: the private consult (PRV-1 to PRV-14). Message text is never stored in plaintext."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal, Protocol

from suwapath.contracts.common import Contract, UntrustedText
from suwapath.contracts.enums import DataCategory, Language
from suwapath.contracts.ids import PrivateSessionId
from suwapath.contracts.principal import Principal


class PrivateSession(Contract):
    id: PrivateSessionId
    started_at: datetime
    retention: Literal["none", "24h", "7d"]
    expires_at: datetime | None
    use_record_categories: frozenset[DataCategory] = frozenset()


class CiphertextMessage(Contract):
    """Encrypted in the browser (PRV-8); the server cannot read it."""

    ciphertext_b64: str
    nonce_b64: str
    created_at: datetime


class PrivateConsultService(Protocol):
    async def start(
        self,
        principal: Principal,
        retention: Literal["none", "24h", "7d"],
        use_record_categories: frozenset[DataCategory],
        disclosure_accepted: bool,
    ) -> PrivateSession: ...

    def turn(
        self,
        principal: Principal,
        session_id: PrivateSessionId,
        recent: list[UntrustedText],
        language: Language,
    ) -> AsyncIterator[str]:
        """Processed in memory only; red flags first through rules (PRV-3, PRV-11)."""
        ...

    async def store(
        self, principal: Principal, session_id: PrivateSessionId, messages: list[CiphertextMessage]
    ) -> None: ...

    async def load(
        self, principal: Principal, session_id: PrivateSessionId
    ) -> list[CiphertextMessage]: ...

    async def delete(self, principal: Principal, session_id: PrivateSessionId) -> None: ...
