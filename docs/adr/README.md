# Architecture Decision Records

Each significant decision is one file: context, decision, consequences, alternatives.
New decisions get the next number in a new file (no edits to accepted records; supersede instead).

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-modular-monolith-with-ports-and-events.md) | Modular monolith with ports and events | Accepted |
| [0002](0002-contracts-package-as-the-only-shared-dependency.md) | Contracts package as the only shared dependency | Accepted |
| [0003](0003-postgres-outbox-and-inbox-instead-of-a-message-broker.md) | Postgres outbox and inbox instead of a message broker | Accepted |
| [0004](0004-case-event-log-is-the-source-of-truth.md) | Case event log is the source of truth; LangGraph checkpoints hold only position | Accepted |
| [0005](0005-process-types-and-skip-locked-queues.md) | Process types and SKIP LOCKED queues | Accepted |
| [0006](0006-durable-timers-and-a-simulated-clock.md) | Durable timers and a simulated clock | Accepted |
| [0007](0007-model-gateway-with-four-adapters-fake-by-default.md) | Model gateway with four adapters, fake by default | Accepted |
| [0008](0008-jev-verifier-that-fails-closed.md) | Jev verifier that fails closed; rules-only escalation without a key | Accepted |
| [0009](0009-structured-retrieval-no-vector-rag-in-the-mvp.md) | Structured retrieval, no vector RAG in the MVP | Superseded by 0018 |
| [0010](0010-fhir-shaped-resources-not-a-fhir-server.md) | FHIR-shaped resources, not a FHIR server | Accepted |
| [0011](0011-per-patient-envelope-encryption.md) | Per-patient envelope encryption | Accepted |
| [0012](0012-consent-as-attribute-based-access-at-the-data-access-layer.md) | Consent as attribute-based access at the data-access layer | Accepted |
| [0013](0013-rest-with-generated-typescript-types-and-sse.md) | REST with generated TypeScript types and SSE | Accepted |
| [0014](0014-frontend-rebuilt-on-our-earlier-design-system.md) | Frontend rebuilt on our earlier design system | Accepted |
| [0015](0015-risk-based-testing-and-a-lean-ci.md) | Risk-based testing and a lean CI | Accepted |
| [0016](0016-mcp-shaped-tools-mcp-server-as-p2.md) | MCP-shaped tools; MCP server as P2 | Accepted |
| [0017](0017-two-pneumonia-models-selected-by-age.md) | Two pneumonia models selected by age, unvalidated until measured | Accepted |
| [0018](0018-knowledge-rag-with-hybrid-retrieval.md) | Knowledge RAG with hybrid retrieval in Postgres | Accepted |
| [0019](0019-llm-only-when-needed.md) | LLM only when needed: cost tiers, intent classifier and two caches | Accepted |

## Template

```
# ADR-NNNN: <title>

Status: Proposed | Accepted | Superseded by ADR-XXXX

## Context
## Decision
## Consequences
## Alternatives considered
```
