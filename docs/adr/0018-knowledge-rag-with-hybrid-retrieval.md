# ADR-0018: Knowledge RAG with hybrid retrieval in Postgres

Status: Accepted (27 Nov 2026). Supersedes ADR-0009.

## Context

Report explanations, care guidance and general health answers must be grounded in trusted knowledge
with citations, in Sinhala and English. ADR-0009 left this to the model alone, which conflicts with
the proposal's promise of explained reports and guided care.

## Decision

A `knowledge` module with a curated, licensed, non-personal health corpus. Structure-aware chunking,
multilingual embeddings behind an `EmbeddingModel` port (local and Vertex adapters), stored in
Postgres with `pgvector` (HNSW) plus full-text search, merged by Reciprocal Rank Fusion. Personal
facts still come from the structured record through consent-checked tools and are never embedded.
Answers cite chunks; the verifier checks claims against the cited chunks.

## Consequences

Grounded, cited answers without new infrastructure. The corpus needs curation and licence checks.
Retrieval quality is measured (Recall@5, MRR, nDCG, context precision, faithfulness).

## Alternatives considered

Model-only answers (ungrounded), a separate vector database such as Qdrant (extra infrastructure),
embedding patient records (a second copy of PHI outside the access policy).
