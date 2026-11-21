# ADR-0008: Jev verifier that fails closed; rules-only escalation without a key

Status: Accepted (27 Nov 2026)

## Context

The proposal requires independent checking with a different model and calibrated confidence.

## Decision

Verifier = deterministic checks plus one Jev Decisions request with noul and choice questions. Low confidence goes to a human. Provider failure holds messages. With no Jev key, messages that pass the rules go to a human reviewer and are labelled.

## Consequences

Never sends unchecked. Keyless demo works honestly but needs human taps.

## Alternatives considered

Same chat model as verifier (not independent), allowing messages when the verifier is down (unsafe).
