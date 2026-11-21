# SuwaPath specifications

Status: **Draft v0.1 for team approval** (27 Nov 2026). Once approved, these files are the contract
every issue is built against.

## How to read these specs

- **MUST**, **MUST NOT**, **SHOULD** and **MAY** are binding, as in RFC 2119.
- Each rule has an ID such as `ARC-3` or `SEC-12`. Issues, tests and pull requests cite these IDs.
- If code and a spec disagree, the spec wins until a pull request changes the spec.
- A spec change is its own pull request with the label `contract-change`, reviewed by the
  foundation owner before any code that depends on it is merged.

## Index

| File | Covers |
|---|---|
| [01-architecture.md](01-architecture.md) | Modules, layers, dependency rules, processes, scalability seams |
| [02-contracts-and-domain.md](02-contracts-and-domain.md) | IDs, enums, Principal, FHIR-shaped resources, the case aggregate, errors |
| [03-events-and-processes.md](03-events-and-processes.md) | Event envelope and catalog, outbox, case event log, workers, timers, clock |
| [04-security-and-privacy.md](04-security-and-privacy.md) | Identity, access control, consent, encryption, PHI minimization, audit |
| [05-agents-and-verifier.md](05-agents-and-verifier.md) | Agent interface, agent communication protocol, the four agents, verifier |
| [06-model-gateway.md](06-model-gateway.md) | Providers, capabilities, budgets, offline mode, prompts |
| [07-clinical-safety.md](07-clinical-safety.md) | Urgency, red flags, emergency lane, wording rules, clash list |
| [08-care-navigation-booking.md](08-care-navigation-booking.md) | Care programmes, adherence, routing, matching, trip planning, booking |
| [09-imaging-and-reports.md](09-imaging-and-reports.md) | Chest X-ray screening, model manifests, lab report reading |
| [10-api.md](10-api.md) | REST conventions, endpoint catalogue, SSE, errors, generated client |
| [11-persistence.md](11-persistence.md) | Tables, indexes, transactions, migrations, files |
| [12-frontend.md](12-frontend.md) | Frontend architecture, data layer, routes, UX and accessibility rules |
| [13-evaluation.md](13-evaluation.md) | CareBench: arms, suites, protocol, statistics |
| [14-quality-and-ci.md](14-quality-and-ci.md) | Test types, Definition of Done, CI pipeline |
| [15-extensions-and-mcp.md](15-extensions-and-mcp.md) | Registries, feature flags, last-day feature playbook, MCP (P2) |
| [16-demo-and-seed.md](16-demo-and-seed.md) | Synthetic world, demo accounts, the Amma scenario |
| [17-knowledge-retrieval-and-caching.md](17-knowledge-retrieval-and-caching.md) | Knowledge RAG, cost and latency tiers, intent classification, shared and user-scoped caches |
| [18-programmes-and-private-consult.md](18-programmes-and-private-consult.md) | Programme catalogue, offerings, referrals, enrolment; the private consult |
| [19-customisable-agents.md](19-customisable-agents.md) | Templates, typed settings in layers, plain-language rules, quotas, guardian suggestions |
| [20-security-and-guardrails.md](20-security-and-guardrails.md) | Authorization decision points, auth hardening, input and output guardrails, frontend and HTTP security, cache security, OWASP LLM Top 10 mapping |
| [ISSUES.md](ISSUES.md) | Work breakdown: waves, dependencies, ownership boundaries, acceptance criteria |

## Non-negotiable constraints

1. SuwaPath guides care. It never diagnoses (`SAF-1`).
2. Consent and urgency floors are enforced in code, never only in prompts (`SEC-20`, `SAF-3`).
3. The verifier fails closed (`VER-9`).
4. The doctor approves every clinical reply (`AGT-30`).
5. Offline LLM mode never simulates imaging or model verification (`GW-14`, `IMG-6`).
6. Model metrics carry their status (`unvalidated`, `reported`, `validated`); no claim beyond the
   evidence, and no sensitivity claim until measured (`IMG-4`).
7. MCP is P2, built only after the core Amma workflow works (`EXT-10`).
8. Every PHI access, including any MCP access, goes through the same consent and audit controls
   (`SEC-21`, `AUD-1`).
9. No credentials, model weights or real patient data in git (`SEC-40`).
