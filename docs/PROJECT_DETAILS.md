# SuwaPath: project details

Team Gmora, University of Moratuwa. INNODATA AI Catalyst '26.

## 1. Objective

Give every person in a patient's care circle (the patient, their family, their doctors and the
hospital) the right view of one encrypted care record, and an AI agent that acts for them, so that
care across many doctors and hospitals stays joined up. SuwaPath guides care. It never diagnoses.

The MVP proves this on one end-to-end elder-care case and measures it against a single-agent design.

## 2. Problem in one paragraph

A 72-year-old woman with diabetes, high blood pressure and arthritis sees three doctors at three
private hospitals. None sees the others' notes, so an allergy or a medicine clash can be missed. She
repeats her history at every visit, and her son abroad learns she has missed her tablets only when she
is admitted. 57.7% of older adults in Kurunegala live with two or more chronic conditions, 18% of Sri
Lankans are 60 or older, and private hospitals deliver 55% of outpatient visits (proposal, section 1).

## 3. Features

| Feature | MVP | What it does |
|---|---|---|
| One connected care record | yes | FHIR-shaped record shared by patient, family, doctors and hospital, each with the right view |
| Guided care navigation | yes | follow-up questions, then specialty, urgency and nearby doctors; several doctors in one trip |
| Emergency fast lane | yes | red flags stop booking and route straight to 1990 |
| Family alerts with consent | yes | the guardian agent checks in with the patient first, then alerts the family within consent |
| Sourced doctor brief | yes | history, medicines, allergies and clashes, with a source on every fact |
| Doctor-approved replies | yes | the doctor approves every clinical reply, delivered in the patient's language |
| Lab reports explained | yes | uploaded reports read and explained in plain language, checked by the verifier |
| Chest X-ray screening | yes | pneumonia screening flag sent to the doctor, never shown to the patient as a result |
| Customisable agents | yes | plain-language rules turned into structured, versioned rules; can only narrow access |
| Private by design | yes | per-patient encryption keys, consent check on every message between roles, full audit |
| AI operations and audit | yes | cost, latency, budgets, verdicts and a tamper-evident audit trail for admins |
| Sinhala and English | yes | text in both languages |
| Care programmes | yes | elder care in full; pregnancy and newborn as working demonstration programmes; platform-owned catalogue, hospital offerings, doctor referrals |
| Private consult | yes | sexual-health questions answered privately: nothing readable stored on the server, optional history encrypted in the browser, automatic deletion |
| Tamil and voice | next | |
| MCP access for AI assistants | next (P2) | tools without patient data first, under the same consent and audit |

## 4. How it works

### People and agents
| Agent | Acts for | Judgement it makes | Asks other agents |
|---|---|---|---|
| Patient companion | patient | what to ask next; which doctors to offer | answers requests from the others |
| Guardian agent | guardian | how serious a week looks; whether to check in, alert or escalate | companion: check in with the patient |
| Doctor assistant | doctor | what the doctor must know; what is missing | companion: confirm a missing fact |
| Verifier | nobody (independent) | allow, revise, block or escalate every cross-role message | returns drafts to their author |

Agents never share memory. They exchange typed messages, each with a consent scope and a trace ID,
and every message that crosses between people passes the verifier.

### Safety rules that code enforces (not prompts)
- Urgency floor from red-flag rules; AI may raise urgency, never lower it.
- Red flag stops booking and routes to 1990.
- Consent decides what each person and agent can read, at the data layer.
- The verifier fails closed: unsure goes to a human, unavailable means held.
- The doctor approves every clinical reply.

## 5. Technical design

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 | typed boundaries, fast to build |
| Agents | LangGraph graphs under an event-driven supervisor | checkpointed, resumable, human pause built in |
| Architecture | modular monolith; contracts package; ports and events | parallel work, late features without breaking working code |
| Events | Postgres outbox and inbox, durable timers, job queues with `SKIP LOCKED` | reliable, scalable, no extra infrastructure |
| Chat model | Gemini on Vertex AI (default); any OpenAI-compatible provider; offline offline mode | provider independence; runs without keys |
| Verifier model | Jev (TypeSafe) via OpenRouter Decisions API | typed decisions with calibrated probabilities |
| Records | PostgreSQL, FHIR R4-shaped resources, FHIR Bundle export | ready for national health records |
| Security | per-patient AES-256-GCM keys wrapped by a KEK (local now, AWS KMS later); attribute-based consent; PHI minimization before model calls; hash-chained audit | a leaked database stays unreadable; least privilege for people and agents |
| Knowledge and cost | curated health corpus, structure-aware chunking, multilingual embeddings, pgvector plus full-text hybrid retrieval with citations; rules, caches and an intent classifier before any chat model call | grounded answers, lower latency and cost |
| Imaging | one ResNet50 pneumonia model on ONNX Runtime, metrics labelled `reported` until re-measured | local, fast, no external calls |
| Frontend | React 19, TypeScript, Tailwind, TanStack Query, generated API types, SSE | fast, accessible, contract-safe |
| Observability | OpenTelemetry traces, structured logs without PHI, model call ledger | every step, cost and delay visible |

Full specifications: [docs/specs/](specs/README.md). Decisions and their reasons:
[docs/adr/](adr/README.md).

## 6. Evaluation (CareBench)

SuwaPath is compared with a single agent and a single agent with a verifier tool, using the same
model, tools, triggers and budget. Suites: routing (300 DDXPlus cases), tool use (50 gold turns),
doctor briefs and retrieval (30 synthetic patients), privacy and injection (60 attacks), red flags
(60 per language), latency and cost, and monitoring. Metrics include specialty and urgency accuracy,
emergency recall, tool selection and argument accuracy, unsupported facts, required-fact recall,
leakage rate, time to first token, p95 latencies and cost per case. Targets were set before testing
([13-evaluation.md](specs/13-evaluation.md)). Results will be published in `docs/results/`.

## 7. Limitations

Synthetic records and simulated hospitals; not a clinical trial; DDXPlus is not Sri Lankan; the
pneumonia metrics are team-reported until our own evaluation re-measures them; automated symptom
checking is less accurate than doctors, so SuwaPath guides and never diagnoses.
