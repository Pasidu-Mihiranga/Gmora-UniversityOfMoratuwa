# SuwaPath

**Your health. Our path.** A multi-agent care platform for Sri Lanka, built by Team Gmora for
INNODATA AI Catalyst '26.

A patient, their family, their doctors and a hospital share one encrypted, consent-scoped care
record. Three person agents act for the patient, the guardian and the doctor, and an independent
verifier checks every message that crosses between them. SuwaPath guides care; it never diagnoses.

- Proposal: [docs/Proposal.pdf](docs/Proposal.pdf)
- Project details: [docs/PROJECT_DETAILS.md](docs/PROJECT_DETAILS.md)
- Workflow: [docs/Workflow.md](docs/Workflow.md)
- Contributor rules: [AGENTS.md](AGENTS.md)

## Deliverables

| Deliverable | Where | Status |
|---|---|---|
| Main application entry point | [project/main.py](project/main.py) | in progress |
| Python dependencies | [project/requirements.txt](project/requirements.txt) | in progress |
| Proposal | [docs/Proposal.docx](docs/Proposal.docx) ([PDF copy](docs/Proposal.pdf)) | done |
| Workflow, process flow, implementation steps | [docs/Workflow.md](docs/Workflow.md) | draft, final with results |
| Objectives, features, functionality, technical details | [docs/PROJECT_DETAILS.md](docs/PROJECT_DETAILS.md) | draft, final with results |
| Demonstration video | `project-demo.mp4` | at the end |
| Specifications (contracts for every feature) | [docs/specs/](docs/specs/README.md) | done |
| Architecture decisions | [docs/adr/](docs/adr/README.md) | done |
| Benchmark pre-registration and sources | `docs/evaluation/` | before the first real-model run |
| Benchmark and scaling results | `docs/results/` | after the evaluation runs |
| Features added on the day | `docs/day-of/` | on the day |

How the judging criteria map to evidence:

| Criterion | Evidence |
|---|---|
| Problem selection and justification, global impact | Proposal sections 1 and 2; PROJECT_DETAILS |
| Mitigation strategy | hazard log ([07](docs/specs/07-clinical-safety.md)), threat model ([04](docs/specs/04-security-and-privacy.md)), PROJECT_DETAILS |
| Multi-agent solution design, tools and technologies | specs [05](docs/specs/05-agents-and-verifier.md), [17](docs/specs/17-knowledge-retrieval-and-caching.md), [19](docs/specs/19-customisable-agents.md); ADRs |
| Single-agent benchmarking and evaluation methodology | spec [13](docs/specs/13-evaluation.md); `docs/evaluation/`; `docs/results/` |
| Independent research | proposal references; `docs/evaluation/SOURCES.md` |
| Features on the day and scaling | spec [15](docs/specs/15-extensions-and-mcp.md); `docs/day-of/`; `docs/results/scaling.md` |

## Getting started

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Node 22, Docker.

```
cp .env.example .env              # then fill in the keys
cd project
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt
docker compose up -d db
python main.py
```

The API runs on http://127.0.0.1:8000 (interactive docs at `/docs`).

## Status

Under active development. Setup, execution guide and demo walkthrough will be completed with the
MVP.
