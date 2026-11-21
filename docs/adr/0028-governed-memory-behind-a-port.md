# ADR-0028: Governed memory behind a port, with deterministic case summaries

Status: Accepted (28 Nov 2026)

## Context

Agents need working, episodic, semantic and procedural memory and personalization. Memory in a
health product is also a privacy liability and must not become a second, less governed record.

## Decision

Working memory stays in case state and the LangGraph checkpoint (execution position only).
Long-term memory goes through a `MemoryService` port in a small `memory` module with encryption,
consent and audit; LangGraph's long-term store is not used for patient data. Case summaries are
built deterministically from case events by template at case close, keep links to their source
events, and are labelled as not a medical record. Learned facts and preferences are stored only after
the person confirms them. A deterministic context builder assembles bounded sections and never
truncates safety or consent information. Each kind of memory is shown where it is used (case
history, record view, settings); a full "my data" page is P2.

## Consequences

Agents remember past episodes without hallucinated summaries or a hidden profile. Retention periods
are design defaults until the PDPA mapping confirms them.

## Alternatives considered

LangGraph PostgresStore for long-term memory (bypasses consent and per-patient encryption),
model-written summaries (can invent facts), automatic learning of facts without confirmation (an
inference silently becoming a record), a separate memory management page now (duplicates existing
screens for little MVP value).
