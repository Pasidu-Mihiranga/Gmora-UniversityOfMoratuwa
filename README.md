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
