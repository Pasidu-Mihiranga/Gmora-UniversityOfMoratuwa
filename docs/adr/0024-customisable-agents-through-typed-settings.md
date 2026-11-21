# ADR-0024: Customisable agents through typed settings in layers

Status: Accepted (27 Nov 2026)

## Context

The proposal lets patients, guardians and doctors shape their own agents. Free-form instructions
would let users weaken safety or inject instructions, and most users prefer tapping to writing.

## Decision

Customisation is a fixed catalogue of typed settings with bounds, in five layers (safety, platform
bounds, organization policy, personal, session) where the most restrictive wins. Templates and
bounded controls are the primary UI; plain language is optional and is parsed into the same settings,
shown back with a preview, and confirmed. Every change is versioned, audited and revertible. Guardians
may suggest changes; patients decide. Per-person daily quotas are enforced atomically in the gateway
and never restrict access to care. The backend is authoritative.

## Consequences

Safe, explainable personalisation that elderly users can operate. New settings need a catalogue entry.

## Alternatives considered

Free-form custom instructions appended to prompts (unsafe, injection risk), no customisation (breaks
the proposal and fits nobody).
