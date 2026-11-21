# ADR-0015: Risk-based testing and a lean CI

Status: Accepted (27 Nov 2026)

## Context

Five days: tests must protect patients and shared state without slowing everyone down.

## Decision

Critical code is test-first with edge cases; normal code gets light tests; one CI workflow with two parallel jobs under 6 minutes.

## Consequences

Fast feedback where it matters. Some UI bugs will be caught by hand.

## Alternatives considered

Coverage gates and full browser e2e in CI (slow, low value this week).
