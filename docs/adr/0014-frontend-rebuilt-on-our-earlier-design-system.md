# ADR-0014: Frontend rebuilt on our earlier design system

Status: Accepted (27 Nov 2026)

## Context

The earlier prototype's design system and screens are good, but its data layer is ad hoc and tied to an old API.

## Decision

Rebuild the app in feature folders, bringing the design tokens and UI kit across piece by piece; TanStack Query and a typed client for all server data; pages rebuilt per feature against the new API.

## Consequences

Consistent look from day one, clean data layer, no dead code.

## Alternatives considered

Copying the old app whole (old API, 160 dashes, unused pages), starting UI from zero (loses good design work).
