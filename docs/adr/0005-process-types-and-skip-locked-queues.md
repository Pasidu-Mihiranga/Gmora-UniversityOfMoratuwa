# ADR-0005: Process types and SKIP LOCKED queues

Status: Accepted (27 Nov 2026)

## Context

The proposal says each agent runs as a separate worker; we need crash recovery without extra infrastructure.

## Decision

api, worker and agents processes from main.py. Jobs and timers claimed with FOR UPDATE SKIP LOCKED and leases.

## Consequences

Any process can run several replicas. One-machine demo still works (python main.py dev).

## Alternatives considered

Celery or RQ (needs Redis), threads inside the API (not scalable, lost on restart).
