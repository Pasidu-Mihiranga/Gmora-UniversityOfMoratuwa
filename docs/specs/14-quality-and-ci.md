# 14. Quality and CI

Five days, five people. Testing effort goes where a bug would hurt a patient, leak data or corrupt
shared state. Everything else gets light tests. No coverage gates, no test theatre.

## 1. Test effort by risk

| Risk tier | Code | What is required |
|---|---|---|
| **Critical** | consent and access policy, encryption, PHI minimization, red flags and urgency floor, verifier, booking concurrency, event delivery idempotency, case aggregate transitions | tests written first; every edge case named in the spec has a test; property tests for the access policy |
| **Important** | agents (with the offline provider), tools, navigation ranking, adherence, timers, API routes | happy path plus the main failure path per use case |
| **Normal** | UI pages, formatting, admin screens | type checks and a smoke render where cheap |

`QA-1` Every port in `contracts/ports` has one **shared contract test suite** in
`contracts/testing/`. Both the real implementation and its test double must pass it. This keeps
test doubles honest, so code built against test doubles works against the real thing.

`QA-2` Model calls in tests use the offline provider or recorded responses. Tests never call paid APIs.
Real providers are exercised only by `python main.py eval`.

`QA-3` Integration tests run against real Postgres (the CI service or local docker compose), not
SQLite.

`QA-4` One API-level end-to-end test of the Amma scenario (and its red-flag and timeout variants)
runs with the offline provider. It is the integration gate for the whole team (issue I02).

`QA-5` Security suites that run in CI: IDOR sweep over every route with a patient ID (another
patient's ID must give 404), consent property tests, PHI egress tests with canary identifiers.
The 60-attack injection suite runs in `eval` with real models, not in CI.

## 2. CI (GitHub Actions)

`QA-10` **One workflow, two parallel jobs, target under 6 minutes.** It runs on pull requests to
`main` and on pushes to `main`. No matrix builds, no nightly jobs, no end-to-end browser tests in CI.

| Job | Steps |
|---|---|
| `backend` | `uv pip install` (cached) -> `ruff check` + `ruff format --check` -> `lint-imports` (module boundaries) -> `alembic upgrade head` + `alembic check` on a Postgres service -> `pytest -x -q` -> OpenAPI drift check |
| `frontend` | `npm ci` (cached) -> `tsc -b` -> `oxlint` -> `vite build` -> generated types drift check |

Plus quick repository checks: `python scripts/check_text.py`, `gitleaks`, `pip-audit` (backend job)
and `npm audit --audit-level=high` (frontend job) (`SG-27`).

`QA-11` Type checking with mypy runs on `suwapath/contracts` and `suwapath/platform` only (the shared
foundation), not on every module.

`QA-12` `main` is protected: pull request required, CI green, one approval from another member.

## 3. Definition of Done (every issue)

- [ ] Acceptance criteria in the issue are met.
- [ ] Spec rules cited in the issue are followed; any spec change went through `contract-change`.
- [ ] Tests at the level required by the risk tier above.
- [ ] CI green.
- [ ] No em or en dashes, no secrets, no PHI in logs.
- [ ] Touched only the paths in the issue's ownership boundary (or explained why in the PR).
- [ ] PR description says what changed and how it was tested, and closes the issue.
