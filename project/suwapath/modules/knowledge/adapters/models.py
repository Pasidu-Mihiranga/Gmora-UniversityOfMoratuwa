"""knowledge tables (KNW-20 to KNW-35). The HNSW index on `embedding` is created in issue K01 once
the embedding model (and so the dimension) is chosen by the retrieval suite (KNW-21)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, Encrypted, UuidKey
from suwapath.platform.db import Base


class DocumentRow(UuidKey, Base):
    __tablename__ = "knowledge_documents"
    title: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(String(400))
    publisher: Mapped[str] = mapped_column(String(120))
    licence: Mapped[str] = mapped_column(String(80))
    language: Mapped[str] = mapped_column(String(2))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))


class ChunkRow(UuidKey, Base):
    __tablename__ = "knowledge_chunks"
    document_id: Mapped[UUID] = mapped_column(index=True)
    language: Mapped[str] = mapped_column(String(2), index=True)
    heading_path: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[Any] = mapped_column(Vector(), nullable=True)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    __table_args__ = (Index("ix_knowledge_chunks_tsv", "tsv", postgresql_using="gin"),)


class AnswerCacheRow(UuidKey, Base):
    """Shared cache keyed by canonical topic (SG-22)."""

    __tablename__ = "knowledge_answer_cache"
    topic_key: Mapped[str] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(2))
    answer: Mapped[str] = mapped_column(Text)
    cited_chunk_ids: Mapped[list[str]] = mapped_column(ARRAY(String(40)))
    corpus_version: Mapped[str] = mapped_column(String(20))
    prompt_version: Mapped[str] = mapped_column(String(20))
    model_id: Mapped[str] = mapped_column(String(80))
    hits: Mapped[int] = mapped_column(Integer, server_default="0")
    verified_at: Mapped[datetime] = mapped_column(TZ)
    expires_at: Mapped[datetime] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    __table_args__ = (UniqueConstraint("topic_key", "language"),)


class UserCacheRow(UuidKey, Base):
    __tablename__ = "knowledge_user_cache"
    patient_id: Mapped[UUID]
    language: Mapped[str] = mapped_column(String(2))
    normalized_question: Mapped[str] = mapped_column(String(500))
    answer_enc: Mapped[bytes] = mapped_column(Encrypted)
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    consent_fingerprint: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    __table_args__ = (
        Index("ix_knowledge_user_cache_lookup", "patient_id", "language", "normalized_question"),
    )
