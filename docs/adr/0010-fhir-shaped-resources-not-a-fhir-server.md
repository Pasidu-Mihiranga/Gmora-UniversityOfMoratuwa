# ADR-0010: FHIR-shaped resources, not a FHIR server

Status: Accepted (27 Nov 2026)

## Context

Interoperability matters for national health records, but a FHIR server is too heavy for five days.

## Decision

Pydantic models following FHIR R4 names and references for the resources we use, stored relationally, with a FHIR Bundle export.

## Consequences

Ready to map to a real FHIR endpoint later. We do not claim full conformance.

## Alternatives considered

HAPI FHIR server (heavy), ad hoc models (no interoperability story).
