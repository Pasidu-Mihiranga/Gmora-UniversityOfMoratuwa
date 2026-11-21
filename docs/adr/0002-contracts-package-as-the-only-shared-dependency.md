# ADR-0002: Contracts package as the only shared dependency

Status: Accepted (27 Nov 2026)

## Context

Parallel work breaks when modules import each other and change under each other.

## Decision

All cross-module types, events and port Protocols live in suwapath.contracts, frozen in Wave 0, with in-memory fakes and shared contract test suites. Modules never import each other; bootstrap wires them.

## Consequences

Developers build against fakes on day one. Contract changes are explicit pull requests. Slight upfront cost to write the contracts.

## Alternatives considered

Shared models inside modules (creates import cycles and hidden coupling).
