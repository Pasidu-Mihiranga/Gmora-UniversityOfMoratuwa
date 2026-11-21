# ADR-0003: Postgres outbox and inbox instead of a message broker

Status: Accepted (27 Nov 2026)

## Context

Events must not be lost or published for rolled-back changes; we have no time to run Kafka or Redis.

## Decision

Transactional outbox in Postgres, dispatcher with SKIP LOCKED and LISTEN/NOTIFY, inbox table for idempotency, retries with backoff, dead letters.

## Consequences

At-least-once delivery with idempotent handlers. The dispatcher can later publish to a broker without module changes.

## Alternatives considered

Redis streams or Kafka (extra infrastructure), in-process callbacks (lost on crash).
