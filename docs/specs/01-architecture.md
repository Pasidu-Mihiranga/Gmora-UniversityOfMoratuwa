# 01. Architecture

## 1. Quality goals (in priority order)

1. **Safety:** no diagnosis, urgency floors and red flags in code, verifier fails closed.
2. **Privacy:** consent enforced at the data-access layer, per-patient encryption, PHI minimized
   before any external model call, everything audited.
3. **Changeability:** a new feature on the last day is added as a new module, subscriber or
   registry entry, without editing working modules.
4. **Parallel development:** five developers build against frozen contracts without blocking.
5. **Responsiveness:** fast perceived and real latency (targets in `13-evaluation.md`).
6. **Scalability seams:** the MVP runs on one machine; nothing in the design prevents scaling out.

## 2. Style

`ARC-1` The backend is a **modular monolith**: one codebase, one database, several process types.

`ARC-2` Modules communicate in exactly two ways:
- **Synchronously** through a **port** (a Python `Protocol` defined in `suwapath.contracts.ports`)
  when the caller needs an answer now.
- **Asynchronously** through an **event** (defined in `suwapath.contracts.events`) when other
  modules react to something that already happened.

`ARC-3` No module imports another module. Modules import only `suwapath.contracts`,
`suwapath.platform` and their own package. The composition root (`suwapath/bootstrap.py`) is the
only place that knows concrete implementations and wires them to ports.

This rule is what makes parallel work safe: a developer builds module X against the ports it needs,
using the in-memory fakes from `suwapath.contracts.testing`, while another developer builds the real
implementation of those ports.

## 3. Code layout

```
project/
  main.py                        CLI entry: api | worker | agents | migrate | seed | eval | openapi | clock | mcp
  requirements.txt               pinned runtime and dev dependencies
  pyproject.toml                 ruff, pytest, mypy, import-linter configuration
  alembic.ini
  migrations/                    Alembic versions (one linear history)
  docker-compose.yml             db (Postgres 16), jaeger, api, worker, agents, web
  Dockerfile
  suwapath/
    contracts/                   the shared language (frozen in Wave 0)
      ids.py  enums.py  errors.py  principal.py  common.py
      fhir/                      FHIR-shaped resource models
      events/                    event envelope and every event payload
      agents.py                  AgentMessage, AgentJob, AgentResult, AgentSpec
      tools.py                   ToolSpec, ToolCall, ToolResult (MCP-shaped)
      api/                       request and response models used by routes
      ports/                     one Protocol per module service
      testing/                   in-memory fakes for every port and shared port test suites
    platform/                    technical kernel, no business rules
      config.py  db.py  uow.py  clock.py  ids.py  logging.py  tracing.py
      http/                      app factory, error mapping, auth dependency, SSE helpers
      events/                    outbox writer, dispatcher, inbox, subscriber registry
      jobs/                      agent job queue, timers
      crypto/                    KeyProvider, envelope encryption, encrypted column type
      gateway/                   model gateway, providers, PHI minimization, budgets
      registry.py  flags.py
    modules/
      identity/  consent/  record/  audit/  safety/  navigation/  booking/  care/
      imaging/  reports/  notifications/  cases/  verifier/  agents/  ops/
      mcp/                       (P2)
    bootstrap.py                 composition root
  tests/
    unit/<module>/  integration/<module>/  contract/  architecture/  security/  e2e/
  eval/                          CareBench (see 13-evaluation.md)
  models/                        model manifests; weights fetched, never committed
  frontend/                      React app (see 12-frontend.md)
```

## 4. Inside a module

```
modules/<name>/
  __init__.py
  module.py          register(container, registries): binds this module's port implementations,
                     routes, subscribers, tools, rules
  domain/            pure Python: entities, value objects, rules. No framework imports.
  application/       use cases (commands and queries). Implements the module's port.
  adapters/          SQLAlchemy repositories, provider clients, file stores
  api/               FastAPI routers. Thin: parse, authorize, call application, map response.
```

`ARC-4` `domain/` MUST NOT import `fastapi`, `sqlalchemy`, `langgraph`, `httpx`, `google`, or any
adapter.
`ARC-5` `api/` MUST call only the module's `application/` layer.
`ARC-6` Every write use case runs inside one `UnitOfWork` (see `11-persistence.md`) so that the
state change, case events, outbox events and audit entries commit or roll back together.

## 5. Dependency rules (enforced in CI by import-linter)

| Rule | Contract |
|---|---|
| `ARC-7` | `suwapath.contracts` imports nothing from `suwapath` except itself |
| `ARC-8` | `suwapath.platform` imports only `suwapath.contracts` and itself |
| `ARC-9` | `suwapath.modules.*` are mutually independent |
| `ARC-10` | only `suwapath.bootstrap` and `main.py` import `suwapath.modules` |
| `ARC-11` | layers inside each module: `api` > `application` > `domain`; `adapters` > `domain` |

## 6. Modules and their responsibility

| Module | Owns | Port (in `contracts/ports`) |
|---|---|---|
| identity | users, credentials, tokens, roles | `IdentityService` |
| consent | consent grants, access policy, customisation rules | `AccessPolicy`, `ConsentService` |
| record | FHIR-shaped clinical record, provenance, patient keys | `RecordService` |
| audit | append-only audit log | `AuditLog` |
| safety | lexicon, red-flag rules, urgency floor, wording rules, clash list | `SafetyService` |
| navigation | specialty routing, doctor matching, trip planning | `NavigationService` |
| booking | sessions, slots, holds, appointments | `BookingService` |
| care | programmes, medication schedules, dose events, alert rules | `CareService` |
| imaging | image model registry, screening | `ImagingService` |
| reports | lab report upload, extraction, explanation drafts | `ReportService` |
| notifications | channels, delivery, quiet hours | `NotificationService` |
| cases | case aggregate, case event log, timeline, case streams | `CaseService` |
| verifier | verifier checks, verdicts, human review queue | `Verifier` |
| agents | agent runtime, registry, the four agents, baseline, tools | `AgentRuntime` |
| ops | budgets, AI operations metrics, dead letters, flags admin | `OpsService` |
| mcp (P2) | MCP server adapter over the tool registry | none |

## 7. Processes

`ARC-12` One codebase, several process types, all started through `main.py`:

| Command | Process | Scales by |
|---|---|---|
| `python main.py api` | FastAPI (uvicorn). Stateless. | more replicas behind a load balancer |
| `python main.py worker` | outbox dispatcher, timers | more replicas (`SKIP LOCKED`) |
| `python main.py agents [--agent KIND]` | agent workers claiming `agent_jobs` | more replicas, or one per agent kind |
| `python main.py migrate` | Alembic upgrade | one-off |
| `python main.py seed [--seed 42]` | load the synthetic world | one-off |
| `python main.py eval ...` | CareBench | one-off |
| `python main.py openapi` | write `openapi.json` for the frontend | one-off |
| `python main.py clock advance 4h` | move the simulated clock (dev and demo only) | one-off |
| `python main.py mcp` (P2) | MCP server over stdio | one per client |

`ARC-13` The API process MUST NOT run background loops. Background work lives in `worker` and
`agents`. For local convenience `python main.py dev` MAY start all three in one process.

## 8. Scalability seams (designed now, built later)

| Concern | MVP | Seam that allows scaling later |
|---|---|---|
| API load | 1 replica | stateless API, JWT, no in-memory session |
| Background work | 1 worker, 1 agent worker | queues claimed with `FOR UPDATE SKIP LOCKED` and leases |
| Event transport | Postgres outbox + `LISTEN/NOTIFY` | dispatcher can publish to Kafka or SQS without touching modules |
| Data volume | single Postgres | every PHI row carries `patient_id` (partition and shard key); audit is time-ordered (range partitioning) |
| Search | Postgres indexes, `pg_trgm`, full text | search adapter behind a port |
| Geo | bounding box plus haversine | PostGIS adapter behind the same port |
| Files | encrypted local file store | S3 adapter behind `FileStore` |
| Keys | local KEK | AWS KMS adapter behind `KeyProvider` |
| Models | provider adapters | any provider behind the gateway |
| Live updates | SSE from the API process | SSE fan-out through `LISTEN/NOTIFY`, then a broker |

`ARC-14` Nothing in the MVP may depend on a single process holding state in memory beyond short
caches (DEK cache, config), because every process type must be safe to run more than once.

## 9. Configuration and wiring modes

`ARC-15` All configuration comes from environment variables (root `.env`) read once by
`platform.config.Settings`. No module reads `os.environ`.

`ARC-16` With no model keys configured the application MUST still start and run the full demo
using the fake chat provider (`06-model-gateway.md`). Real keys only change which adapter is bound.
