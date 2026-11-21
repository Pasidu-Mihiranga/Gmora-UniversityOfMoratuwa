# ADR-0012: Consent as attribute-based access at the data-access layer

Status: Accepted (27 Nov 2026)

## Context

Roles alone cannot express 'my son sees my medicines but not my chats'.

## Decision

Role checks on routes plus AccessPolicy.decide() inside the record data-access layer for every query; agents get the intersection of their human's access and their own scope; deny by default; no cross-request caching.

## Consequences

Consent cannot be bypassed by an API or agent bug. Revocation is immediate.

## Alternatives considered

Postgres row-level security (strong but too costly to build and debug in five days), checks only in routes (bypassable).
