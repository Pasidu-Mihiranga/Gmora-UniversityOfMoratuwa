# ADR-0026: pgvector now; Qdrant as a later adapter behind the same interface

Status: Accepted (28 Nov 2026). Refines ADR-0018.

## Context

The team asked whether the knowledge corpus should live in Qdrant instead of pgvector. The corpus is
about 60 curated documents (a few thousand chunks). Retrieval combines vector and full-text search,
and the shared and user caches live next to it. An embedded, in-process vector index would be lost
on every rebuild and have to be re-ingested at startup.

## Decision

Keep documents, chunks and embeddings in PostgreSQL with pgvector (HNSW) and full-text indexes. A
chunk and its vector are written in one transaction, survive restarts and rebuilds in the database
volume, and are covered by the same backups as everything else.

All retrieval goes through `KnowledgeService.retrieve()`; no agent, cache or API code knows where
vectors live. If the corpus grows to millions of chunks, needs heavy multi-tenant filtering or vector
compression, a Qdrant adapter implements the same interface, the corpus is re-ingested into it, and a
setting switches the binding. Nothing else changes.

## Consequences

One database to run, secure and back up; no dual-write synchronisation; hybrid search in one query.
The Qdrant path stays open at the cost of one adapter and a re-ingest.

## Alternatives considered

Qdrant now (a second store to keep in sync with Postgres, extra container, key and backups, with no
benefit at this scale), an in-process index (lost on every rebuild).
