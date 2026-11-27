# ADR-0014: Frontend on a token-based design system with a typed data layer

Status: Accepted (27 Nov 2026)

## Context

Five people build screens in parallel. The product must look consistent, work in simple mode for
older users, and never drift from the API.

## Decision

Feature folders on one design system: design tokens as the single source of truth for colour,
type, spacing and sizes; a shared UI kit; TanStack Query and a typed client generated from OpenAPI
for all server data. Pages are built per feature against the API.

## Consequences

Consistent look from day one, simple mode by changing tokens only, a clean data layer, and type
errors instead of runtime failures when the API changes.

## Alternatives considered

Ad hoc styling per page (inconsistent, no simple mode), a third-party component library (a second
design language to fight), hand-written fetch code (drift from the API).
