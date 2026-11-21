# ADR-0001: Modular monolith with ports and events

Status: Accepted (27 Nov 2026)

## Context

Five developers, five days, one product with strong boundaries (consent, safety) and a need to add features late.

## Decision

One codebase and database, split into modules that talk only through ports (sync) and events (async). Several process types from the same code.

## Consequences

Low infrastructure cost, clear ownership, boundaries checked in CI. Scaling is by process replicas, not services.

## Alternatives considered

Microservices (too much deployment and network cost), a plain layered app (no isolation between features).
