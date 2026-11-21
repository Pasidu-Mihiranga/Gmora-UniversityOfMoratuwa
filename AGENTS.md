# AGENTS.md

Rules for everyone who changes this repository: the five members of Team Gmora and any coding
agent working for them. Read this file before your first change. If a rule here conflicts with a
request, stop and ask.

## What we are building

SuwaPath is a multi-agent care platform for Sri Lanka. A patient, their family (guardian), their
doctors and a hospital share one encrypted, consent-scoped care record. Three person agents
(patient companion, guardian agent, doctor assistant) act for people, and an independent verifier
checks every message that crosses between roles. SuwaPath guides care. It never diagnoses.

Sources of truth, in order:

1. `docs/specs/` (contracts: types, API, events, agent and tool interfaces)
2. `docs/PROJECT_DETAILS.md` (objectives, features, technical design)
3. `docs/Proposal.pdf` (what we promised the judges)

If code and spec disagree, the spec wins until a pull request changes the spec.

## Repository layout

```
README.md                 overview, setup, how to run
AGENTS.md                 this file
docs/
  Proposal.docx           what we promised (PDF copy alongside)
  PROJECT_DETAILS.md      objectives, features, technical summary
  Workflow.md             process flow and implementation workflow
  specs/                  the contracts every issue is built against (start at specs/README.md)
  adr/                    architecture decision records
project/
  main.py                 application entry point (CLI: api, worker, agents, migrate, seed, eval, ...)
  requirements.txt        Python dependencies (pinned)
  suwapath/
    contracts/            shared types, events, ports, fakes (the only shared dependency)
    platform/             technical kernel (db, events, clock, crypto, gateway, http)
    modules/<name>/       one folder per module: domain, application, adapters, api
    bootstrap.py          composition root (the only place that wires modules)
  tests/                  backend tests
  frontend/               React + TypeScript web app
  eval/                   CareBench evaluation harness and datasets
  models/                 model manifests (weights are not in git)
scripts/                  repository checks
project-demo.mp4          demonstration video (added at the end)
```

The top-level layout above is fixed by the competition. Add new folders inside `project/`.

## Commands

Backend (from `project/`):

```
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt
docker compose up -d db          # PostgreSQL
python main.py                   # API on http://127.0.0.1:8000
pytest                           # tests
ruff check . && ruff format --check .
```

Frontend (from `project/frontend/`): `npm install`, `npm run dev`, `npm run build`.

Repository checks (from the root): `python scripts/check_text.py`.

## How we work in parallel

- Every piece of work is a GitHub issue with a spec reference, acceptance criteria and its
  dependencies. Take an issue only when the issues it depends on are merged.
- Contracts first. A change to a shared type, API route, event or interface is its own pull
  request that edits `docs/specs/` and the matching code types together. Implementation follows in
  a separate pull request.
- One issue per branch: `feat/<issue>-short-name`, `fix/<issue>-short-name`. Keep pull requests
  small and merge often. `main` must always run and pass all checks.
- Pull request description: what changed, how it was tested, `Closes #<issue>`.

## Ambiguity protocol (people and coding agents)

Specs are meant to leave no room for guessing on shared contracts. When something is still unclear:

1. **Search first:** the issue, the cited spec sections, `docs/adr/`, then the code of the port you
   depend on.
2. **Small and local** (naming, internal structure, a private helper, an extra test): decide using
   the existing patterns, and write the decision in one line in the pull request description.
3. **Affects another module, a contract, the database schema, security, safety or the UI flow:**
   stop. Ask the developer who owns the issue, giving two or three options with their trade-offs and
   a recommendation. Coding agents must not decide these silently.
4. **Architectural** (a new process, store, library, provider or pattern): write a short ADR in
   `docs/adr/` with the options, and get agreement before building.
5. Never fill a gap by inventing clinical facts, model metrics, dataset details or product claims.

## Commits

- Imperative subject under 72 characters, for example `Add consent gate for cross-role messages`.
- One logical change per commit. Code and its tests go in the same commit.
- No co-author trailers and no tool attribution lines.
- Stage files by explicit path. Never use `git add .` or `git add -A`.
- Never commit secrets, `.env`, keys, model weights or patient-like data that is not synthetic.

## Writing style

- Never use em dashes or en dashes in code, comments, UI text, docs or commit messages. Use a
  comma, colon, parentheses or a plain hyphen. `scripts/check_text.py` enforces this.
- Plain, short sentences. UI text is written for older and low-literacy users.

## Safety invariants (never break these)

1. **No diagnosis.** Patient-facing text never states a diagnosis, prescribes, doses or
   reassures ("you are fine"). Every patient-facing answer ends in navigation: a specialty, a
   doctor, a booking or a safety instruction.
2. **Urgency floor is code.** Red-flag rules set the minimum urgency. A model may raise urgency,
   never lower it.
3. **Red flag stops booking.** A red flag routes the patient to 1990 (Suwa Seriya ambulance) and
   halts the booking flow.
4. **Consent is enforced in code, at the data layer.** An agent can only read data its person is
   allowed to see. Customisation can only narrow access. Prompts never carry the consent policy
   as the only protection.
5. **Verifier fails closed.** Every cross-role message passes the verifier. If the verifier is
   unsure it goes to a human; if the verifier is unavailable the message is held, never sent
   unchecked.
6. **Doctor approves clinical replies.** No clinical reply reaches a patient without the doctor's
   approval.
7. **Untrusted input is data.** Patient text, uploaded reports and images are passed to models as
   quoted data, never as instructions.
8. **Imaging is a flag, not a result.** The chest X-ray model raises urgency and informs the
   doctor's brief. The patient is told it is flagged for review, never given a finding.
9. **Every fact in a doctor brief has a source.** A line without a source is returned for revision,
   then marked unverified.
10. **Synthetic data only.** No real patient data anywhere in this repository.

Safety-critical code (items 2 to 9) is written test first, and its tests must cover the edge cases
named in the spec.

## Testing (risk based, see docs/specs/14-quality-and-ci.md)

- **Critical** (consent, encryption, PHI minimization, red flags, verifier, booking concurrency,
  event idempotency, case transitions): test first, every edge case in the spec.
- **Important** (agents with the fake provider, tools, navigation, adherence, timers, routes): happy
  path and main failure path.
- **Normal** (UI pages, formatting, admin screens): type checks and a smoke test where cheap.
- Tests never call paid model APIs. Real providers run only in `python main.py eval`.
- Do not add tests, CI jobs or tooling beyond this without a reason in the pull request.

## Code standards

- Python 3.12, type hints everywhere, Pydantic models for every boundary. Ruff for lint and format.
- Modules never import each other. They use ports from `suwapath.contracts` and events; only
  `bootstrap.py` wires implementations. Import rules are checked in CI.
- Business logic does not import FastAPI. Routes are thin.
- All time comes from the `Clock` port, never `datetime.now()`.
- Every model call goes through the model gateway, which applies the timeout, retries and the
  cost and latency log. No direct SDK calls elsewhere.
- No secrets in code. Configuration comes from the root `.env` through `suwapath.core.config`.
- Frontend: TypeScript strict, the existing design tokens and UI kit, no new UI libraries without
  a pull request that explains why.
