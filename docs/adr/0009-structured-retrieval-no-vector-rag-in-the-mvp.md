# ADR-0009: Structured retrieval, no vector RAG in the MVP

Status: Accepted (27 Nov 2026)

## Context

The doctor brief needs exact facts with sources, filtered by consent; a vector index adds a second copy of PHI outside the access policy.

## Decision

Agents retrieve facts through consent-checked tools over the structured record. Evaluation measures required-fact recall, precision and source accuracy.

## Consequences

Exact sources, one access path. If a guidance corpus is added later, its PR must add retrieval metrics (recall@k, MRR, nDCG, faithfulness).

## Alternatives considered

Qdrant or pgvector over records (duplicates PHI, harder to enforce consent).
