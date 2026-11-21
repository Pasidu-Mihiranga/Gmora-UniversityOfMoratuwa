# ADR-0016: MCP-shaped tools; MCP server as P2

Status: Accepted (27 Nov 2026)

## Context

Interoperability with AI assistants is valuable, but exposing patient data adds risk.

## Decision

Tools use JSON Schema input and output from day one. An MCP server adapter over the registry is built after the core flow works, starting with tools that touch no patient data, under the same principal, consent and audit.

## Consequences

Almost free now, half a day later. No separate path to data.

## Alternatives considered

Building MCP first (nothing to expose yet), never building it (loses an interoperability story).
