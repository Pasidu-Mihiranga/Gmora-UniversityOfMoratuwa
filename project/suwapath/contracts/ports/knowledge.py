"""knowledge: corpus retrieval, caches, intent classification (KNW-1 to KNW-36)."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field

from suwapath.contracts.common import Contract, UntrustedText
from suwapath.contracts.enums import Intent, Language
from suwapath.contracts.ids import PatientId
from suwapath.contracts.principal import Principal


class RetrievedChunk(Contract):
    chunk_id: str
    document_title: str
    heading_path: str
    language: Language
    text: str  # public corpus text, not PHI (KNW-10)
    source_url: str
    licence: str
    score: float


class IntentSlots(Contract):
    doctor_name: str | None = None
    specialty: str | None = None
    hospital: str | None = None
    date_window: Literal["today", "tomorrow", "this_week", "date"] | None = None
    date: str | None = None
    time_of_day: Literal["morning", "afternoon", "evening", "any"] | None = None
    language: Language | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class IntentResult(Contract):
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    slots: IntentSlots = IntentSlots()
    missing_slots: tuple[str, ...] = ()
    classifier: Literal["jev", "keywords"]


class IntentClassifier(Protocol):
    async def classify(self, text: UntrustedText, language: Language) -> IntentResult:
        """Runs after the T0 safety screen, never instead of it (KNW-3a)."""
        ...


class CachedAnswer(Contract):
    answer: str
    cited_chunk_ids: tuple[str, ...]
    scope: Literal["shared", "user"]


class KnowledgeService(Protocol):
    async def retrieve(
        self, query: str, language: Language, k: int = 5
    ) -> list[RetrievedChunk]: ...

    async def shared_lookup(
        self, question: str, language: Language, intent: Intent
    ) -> CachedAnswer | None: ...

    async def shared_store(
        self,
        question: str,
        language: Language,
        intent: Intent,
        intent_confidence: float,
        answer: str,
        cited_chunk_ids: list[str],
        verified: bool,
    ) -> bool:
        """Stores only if KNW-32 holds; returns whether it was stored."""
        ...

    async def user_lookup(
        self, principal: Principal, patient_id: PatientId, question: str, language: Language
    ) -> CachedAnswer | None:
        """Valid only if source and consent fingerprints still match (KNW-34)."""
        ...

    async def user_store(
        self,
        principal: Principal,
        patient_id: PatientId,
        question: str,
        language: Language,
        answer: str,
        source_resource_versions: dict[str, int],
    ) -> None: ...
