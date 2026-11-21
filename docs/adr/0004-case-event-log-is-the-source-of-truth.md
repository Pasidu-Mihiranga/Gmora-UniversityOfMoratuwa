# ADR-0004: Case event log is the source of truth; LangGraph checkpoints hold only position

Status: Accepted (27 Nov 2026)

## Context

Two stores of case state would drift apart, especially across the 4 hour doctor pause.

## Decision

cases_events is append-only with optimistic locking; LangGraph checkpoints store execution position only; nodes re-read the case before deciding.

## Consequences

One truth, replayable history, safe resumes. Nodes pay a small read cost.

## Alternatives considered

Keeping facts in LangGraph state (opaque, hard to audit, stale after pauses).
