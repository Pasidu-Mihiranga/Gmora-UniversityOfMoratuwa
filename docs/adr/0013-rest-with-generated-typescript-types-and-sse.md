# ADR-0013: REST with generated TypeScript types and SSE

Status: Accepted (27 Nov 2026)

## Context

Frontend and backend are built in parallel; silent contract drift would break the demo.

## Decision

REST with OpenAPI from FastAPI, committed openapi.json and generated types, CI drift check. Live updates by SSE with one-time stream tickets and Last-Event-ID replay.

## Consequences

Breaking changes fail the build. No polling where a stream exists.

## Alternatives considered

GraphQL (new tooling), WebSockets (bidirectional not needed), polling (slow and wasteful).
