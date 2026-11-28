# 10. API

## 1. Conventions

`API-1` Base path `/api/v1`. JSON in and out, `snake_case` fields, UTC ISO 8601 timestamps.

`API-2` Request and response models live in `suwapath/contracts/api/`. Routes declare
`response_model`. FastAPI generates the OpenAPI document.

`API-3` **Generated client.** `python main.py openapi` writes `project/frontend/src/shared/api/openapi.json`;
`npm run gen:api` turns it into `openapi.d.ts` (openapi-typescript). Both files are committed. CI
regenerates them and fails if they differ (contract drift check).

`API-4` Errors use RFC 9457 problem details:
`{"type": "https://suwapath.dev/errors/<code>", "title", "status", "detail", "code", "trace_id"}`.
`code` is the `SuwaPathError.code` from `02-contracts-and-domain.md`.

`API-5` Lists use cursor pagination: `?limit=20&cursor=<opaque>`, response
`{"items": [...], "next_cursor": "..." | null}`. Max `limit` 100. No offset pagination.

`API-6` Creating requests that must not happen twice accept an `Idempotency-Key` header
(booking confirm, dose taken, doctor decision, consent change). Same key and body within 24 h
returns the original response.

`API-7` Case writes from clients carry `expected_version` where a stale view could cause a wrong
decision (doctor decision, case close). Mismatch returns 409 `version_conflict`.

`API-8` Every response carries `X-Trace-Id`. Every route has an explicit role list (`SEC-10`).

## 2. Endpoint catalogue (MVP)

Roles: P patient, G guardian, D doctor, H hospital_admin, A system_admin. "own" means the
patient's own data; "consented" and "treating" refer to `SEC-10`.

### Auth and me
| Method and path | Roles | Purpose |
|---|---|---|
| `POST /auth/login` | public | email or phone + password, returns access token, sets refresh cookie |
| `POST /auth/refresh` | cookie | rotate refresh, new access token |
| `POST /auth/logout` | any | revoke refresh family |
| `GET /me` | any | profile, role, language, linked patients (guardian), flags |
| `PATCH /me` | any | language, quiet hours, dose slot times (patient) |
| `POST /stream-tickets` | any | one-time SSE ticket (`SEC-5`) |

### Patients, record and consent
| Method and path | Roles | Purpose |
|---|---|---|
| `GET /patients/{pid}/summary` | P own, G consented, D treating | record summary filtered by access |
| `GET /patients/{pid}/resources?type=&cursor=` | P own, G consented, D treating | resources of one type |
| `GET /patients/{pid}/fhir/export` | P own | FHIR R4 Bundle |
| `GET /patients/{pid}/consents` | P own | grants per guardian and doctor |
| `PUT /patients/{pid}/consents/{grantee_id}` | P own | set categories (narrow or widen by the patient only) |
| `DELETE /patients/{pid}/consents/{grantee_id}` | P own | revoke |
| `POST /rules/parse` | P, G, D | plain-language text -> `RuleProposal` (not saved) |
| `POST /rules` | P, G, D | confirm a proposal (versioned) |
| `GET /rules` | P, G, D | my rules with versions |

### Cases and companion
| Method and path | Roles | Purpose |
|---|---|---|
| `POST /cases` | P | open a case with the first message |
| `GET /cases?status=&cursor=` | P own, G consented, D assigned | my cases |
| `GET /cases/{cid}` | same | case state (filtered) |
| `GET /cases/{cid}/timeline?cursor=` | same | events and messages, filtered by access |
| `POST /cases/{cid}/messages` | P | patient message (answers, follow-ups) |
| `GET /cases/{cid}/stream?ticket=` | same | SSE |

### Care
| Method and path | Roles | Purpose |
|---|---|---|
| `GET /patients/{pid}/doses?date=` | P own, G consented | today's schedule with status |
| `POST /patients/{pid}/doses/{dose_id}/taken` | P own | mark taken (idempotent) |
| `GET /patients/{pid}/adherence?days=7` | P own, G consented, D treating | summary |
| `GET /programmes` | any | available programmes |
| `POST /patients/{pid}/enrollments` | P own | join a programme |

### Programmes and private consult
| Method and path | Roles | Purpose |
|---|---|---|
| `GET /programmes/{key}/offerings?near=&cursor=` | P, G, D | approved offerings by distance and next session |
| `POST /patients/{pid}/enrollments` | P own | join (self or from a referral, optional offering) |
| `GET /patients/{pid}/enrollments` | P own, G consented, D treating | enrolments |
| `POST /doctor/referrals` | D treating | refer a patient to a programme or offering |
| `GET /patients/{pid}/referrals`, `POST /referrals/{id}/accept`, `POST /referrals/{id}/decline` | P own | referrals |
| `GET /hospital/offerings`, `POST /hospital/offerings`, `PATCH /hospital/offerings/{id}` | H own org | manage offerings, submit for approval |
| `GET /admin/programmes`, `PUT /admin/programmes/{key}` | A | catalogue: enable or disable types |
| `GET /admin/offerings?status=pending_approval`, `POST /admin/offerings/{id}/decision` | A | approve or reject |
| `POST /private/sessions` | P | start (after disclosure); returns session id |
| `POST /private/sessions/{id}/turns` | P | one turn, processed in memory, streamed reply |
| `PUT /private/sessions/{id}/messages` | P | store client-encrypted ciphertext (opt-in only) |
| `GET /private/sessions/{id}/messages` | P | ciphertext for the device to decrypt |
| `DELETE /private/sessions/{id}` | P | delete now |

### Navigation and booking
| Method and path | Roles | Purpose |
|---|---|---|
| `GET /specialties` | any | catalog with si and en names |
| `GET /doctors?specialty=&near=&from=&to=&lang=&cursor=` | P, G | search (debounced on the client) |
| `GET /doctors/{id}/slots?from=&to=` | P, G | free slots |
| `POST /trips/plan` | P | itineraries for several specialties |
| `POST /bookings/holds` | P | hold a slot (5 min) |
| `POST /bookings/holds/{hold_id}/confirm` | P | confirm (Idempotency-Key) |
| `DELETE /bookings/{appointment_id}` | P own, H own org | cancel |
| `GET /bookings?cursor=` | P own | my appointments |
| `GET /hospital/sessions?date=` | H | sessions with counts |
| `GET /hospital/sessions/{sid}/appointments` | H | list (first name, number, status) |

### Guardian
| Method and path | Roles | Purpose |
|---|---|---|
| `GET /guardian/dependents` | G | linked patients and their consented overview |
| `GET /guardian/alerts?cursor=` | G | alerts |
| `POST /guardian/alerts/{id}/ack` | G | acknowledge |
| `GET /guardian/rules`, `POST /guardian/rules` | G | alert rules (structured or via `/rules/parse`) |

### Doctor
| Method and path | Roles | Purpose |
|---|---|---|
| `GET /doctor/queue?date=` | D | today's patients with brief status and urgency |
| `GET /doctor/cases/{cid}/brief` | D treating | sourced brief |
| `GET /doctor/sources/{provenance_id}` | D treating | the source behind a brief line |
| `POST /doctor/cases/{cid}/notes` | D treating | notes for the reply draft |
| `POST /doctor/cases/{cid}/request-fact` | D treating | ask the patient for a missing fact |
| `GET /doctor/approvals` | D | drafts waiting |
| `POST /doctor/approvals/{draft_id}` | D | `approve`, `edit` (with text), `reject` (Idempotency-Key, expected_version) |

### Reports and imaging
| Method and path | Roles | Purpose |
|---|---|---|
| `POST /patients/{pid}/reports` | P own, D treating | multipart upload |
| `GET /patients/{pid}/reports?cursor=` | P own, G consented, D treating | list |
| `GET /reports/{rid}` | same | extraction, observations, explanation |
| `POST /patients/{pid}/imaging` | P own, D treating | multipart upload |
| `GET /imaging/{sid}` | P own (status only), D treating (full) | result |

### Reviews and notifications
| Method and path | Roles | Purpose |
|---|---|---|
| `GET /reviews` | P, D | messages waiting for my review (`VER-5`) |
| `POST /reviews/{id}` | P, D | `send`, `edit`, `discard` |
| `GET /notifications?cursor=` | any | my notifications |
| `POST /notifications/{id}/read`, `POST /notifications/read-all` | any | |
| `GET /me/stream?ticket=` | any | SSE for notifications and alerts |

### Ops, audit and dev
| Method and path | Roles | Purpose |
|---|---|---|
| `GET /ops/overview` | A | provider mode (real or simulated), cost and latency today, budgets, queue depths |
| `GET /ops/model-calls?case_id=&agent=&cursor=` | A | call log (no content) |
| `GET /ops/budgets`, `PUT /ops/budgets/{scope}` | A | budgets |
| `GET /ops/dead-letters`, `POST /ops/dead-letters/{id}/replay` | A | failed deliveries |
| `GET /ops/flags`, `PUT /ops/flags/{key}` | A | feature flags |
| `GET /audit?patient_id=&actor_id=&action=&from=&to=&cursor=` | A | audit search (no PHI in entries) |
| `GET /dev/clock`, `POST /dev/clock/advance` | dev and demo only | simulated clock |
| `POST /dev/scenario/reset` | dev and demo only | reload the seed world |
| `GET /health/live`, `GET /health/ready` | public | readiness checks DB, outbox lag, providers |

## 3. Server-sent events

`API-20` Streams: `/cases/{cid}/stream` and `/me/stream`. Each event has `id` (the outbox event ID or
case event ID), `event` (type) and `data` (JSON, filtered by the subscriber's access). Clients
reconnect with `Last-Event-ID`; the server replays missed events from the case log or notifications
table. Heartbeat comment every 15 s.

| SSE event | Data |
|---|---|
| `case.updated` | case id, new version, status, urgency |
| `case.timeline_appended` | timeline item (filtered) |
| `agent.progress` | agent kind, step label key (for "Checking urgency...", "Finding doctors...") |
| `message.delivered` | message summary for the recipient |
| `doctor.approval_required` | draft id |
| `notification.created` | notification |
| `booking.updated` | appointment id, status |
| `document.processed` | document id, kind (`report`, `imaging`), status |

`API-21` The API process listens on Postgres `LISTEN suwapath_stream` to fan out to its open streams,
so any API replica can serve any stream.

`API-22` A companion message to the patient is a `CompanionReply` (`contracts/agents.py`) stored as
JSON in the case communication and returned in the timeline item's `params.text`:
`{"kind": "question" | "clarify" | "result" | "emergency" | "check_in" | "info", "text": "...",
"language": "si" | "en", "suggestions": [Suggestion, ...]}`. `text` is complete and human
readable on its own; clients show it as the message and the suggestions as chips (`FE-50`).
Patient messages stay plain text.
