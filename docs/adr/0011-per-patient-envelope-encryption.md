# ADR-0011: Per-patient envelope encryption

Status: Accepted (27 Nov 2026)

## Context

A leaked database must stay unreadable; patients may ask for erasure.

## Decision

One AES-256-GCM data key per patient, wrapped by a KEK behind KeyProvider (local now, AWS KMS later). Sensitive fields and files encrypted with associated data binding them to their row.

## Consequences

Crypto-shredding possible. Encrypted fields cannot be searched; query fields stay plain.

## Alternatives considered

Database-level encryption only (does not protect against application-level leaks), one global key (no per-patient isolation).
