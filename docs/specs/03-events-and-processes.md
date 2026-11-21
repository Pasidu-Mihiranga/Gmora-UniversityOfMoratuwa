# 03. Events, workers, timers and the clock

## 1. Two stores, two jobs

| Store | Table | Purpose | Source of truth for |
|---|---|---|---|
| Case event log | `cases_events` | append-only history of one case, rebuilt into `CaseState` | everything about a case |
| Outbox | `platform_outbox` | reliable notification of other modules | nothing (it is a delivery queue) |

`EVT-1` A use case that changes state writes its table rows, its case events (if any), its outbox
events and its audit entries **in one transaction** through `UnitOfWork`. There is no code path that
publishes an event outside a transaction.

`EVT-2` Case events are internal to the `cases` module. When another module must react, `cases`
also writes the matching integration event to the outbox in the same transaction.

## 2. Event envelope

```python
class EventEnvelope(BaseModel):
    event_id: EventId               # UUIDv7, unique
    type: str                       # "<module>.<past_tense>", e.g. "care.dose_missed"
    version: int                    # payload schema version, starts at 1
    occurred_at: datetime           # from Clock
    producer: str                   # module name
    aggregate_id: str               # case id, appointment id, ...
    patient_id: PatientId | None    # partition key, never more than an ID
    correlation_id: CaseId | None   # the case this belongs to, if any
    causation_id: EventId | None    # the event that caused this one
    trace_id: TraceId
    payload: BaseModel              # the typed payload for (type, version)
```

`EVT-3` **Payloads carry IDs, enums and numbers, never PHI.** A subscriber that needs clinical data
fetches it through a port using its own `Principal`, so consent is evaluated at read time.

`EVT-4` Payload changes inside a version are additive only (new optional fields). A breaking change
creates `version + 1`; consumers declare which versions they accept.

## 3. Event catalog (v1)

| Type | Producer | Key payload fields | Consumers |
|---|---|---|---|
| `identity.user_registered` | identity | user_id, role | notifications |
| `consent.grant_changed` | consent | patient_id, grantee_id, categories, change (`granted`, `revoked`, `narrowed`) | agents (revalidate), notifications |
| `consent.rule_confirmed` | consent | rule_id, owner_id, version | agents |
| `consent.setting_changed` | consent | owner_id, setting_key, version, affects_user_ids | agents, notifications |
| `consent.suggestion_created` | consent | suggestion_id, patient_id, guardian_id, setting_key | notifications |
| `record.resource_written` | record | resource_type, resource_id, patient_id | agents (doctor assistant refresh) |
| `reports.report_uploaded` | reports | document_id, patient_id | reports (extraction) |
| `reports.report_extracted` | reports | report_id, observation_ids, patient_id | cases, agents |
| `imaging.study_uploaded` | imaging | study_id, patient_id | imaging (screening) |
| `imaging.screening_completed` | imaging | study_id, band, flagged (bool), model_version, validation_status | cases (urgency floor), agents |
| `care.dose_taken` | care | administration_id, statement_id, patient_id | agents (guardian) |
| `care.dose_missed` | care | administration_id, statement_id, slot (`morning`, `noon`, `evening`, `night`), patient_id | agents (guardian) |
| `care.alert_rule_fired` | care | rule_id, rule_type, patient_id, guardian_id | agents (guardian) |
| `care.enrolled` | care | enrollment_id, patient_id, programme_key, offering_id | notifications |
| `care.referral_created` | care | referral_id, patient_id, programme_key, referred_by | notifications |
| `booking.slot_held` | booking | hold_id, slot_id, expires_at, case_id | cases |
| `booking.appointment_booked` | booking | appointment_id, practitioner_role_id, case_id | cases, agents (doctor assistant), notifications |
| `booking.appointment_cancelled` | booking | appointment_id, by (`patient`, `hospital`, `system`), case_id | cases, agents |
| `cases.case_opened` | cases | case_id, patient_id, opened_by | agents (companion) |
| `cases.patient_message_received` | cases | case_id, communication_id | agents (companion) |
| `cases.urgency_assessed` | cases | case_id, urgency, floor | agents, notifications |
| `cases.red_flag_raised` | cases | case_id, rule_ids, urgency | booking (cancel holds), agents (guardian), notifications |
| `cases.doctor_approval_requested` | cases | case_id, draft_id, practitioner_id, due_at | notifications |
| `cases.doctor_decision_recorded` | cases | case_id, draft_id, decision (`approved`, `edited`, `rejected`) | agents (resume) |
| `cases.reply_delivered` | cases | case_id, communication_id | notifications |
| `cases.handed_to_human` | cases | case_id, reason | notifications, ops |
| `agents.message_sent` | agents | message_id, kind, from_agent, to (agent or person), case_id | agents, verifier |
| `verifier.verdict_issued` | verifier | message_id, verdict, decided_by (`rules`, `model`, `human`) | agents, cases |
| `notifications.delivered` | notifications | notification_id, channel | ops |
| `ops.budget_exceeded` | ops | scope (`case`, `agent`, `daily`), case_id | cases |
| `platform.timer_fired` | platform | timer_id, kind, ref | owner module of the timer kind |

## 4. Delivery: outbox dispatcher

`EVT-5` The dispatcher (in the `worker` process) claims batches:

```sql
SELECT ... FROM platform_outbox
WHERE status = 'pending' AND available_at <= now()
ORDER BY event_id
LIMIT 100
FOR UPDATE SKIP LOCKED;
```

It is woken by `LISTEN suwapath_outbox` (a `NOTIFY` is sent on commit) and also polls every 1 s as
a fallback.

`EVT-6` **Inbox idempotency.** For each (event, subscriber) pair the handler runs in its own
transaction that first inserts `(event_id, subscriber)` into `platform_inbox` (unique). If the row
exists, the handler is skipped. Handlers MUST also be written to be safe if run twice.

`EVT-7` **Retries.** A failing handler is retried with exponential backoff and jitter:
1 s, 2 s, 4 s, 8 s, 16 s, 32 s, 64 s, 128 s (8 attempts). Then the (event, subscriber) pair moves
to `dead` with the last error, is shown on the ops page and can be replayed by an admin.

`EVT-8` **Ordering.** Global order is not guaranteed. Handlers use aggregate versions or state
checks, never arrival order. Within one case, the case aggregate's version is the order.

`EVT-9` Subscribers register by type in `module.register()`:
`registries.subscribers.add("care.dose_missed", handler, name="agents.guardian_trigger", versions={1})`.

## 5. Agent job queue

`EVT-10` Work for agents is a row in `agents_jobs`:
`(id, agent_kind, case_id, patient_id, trigger_event_id, principal_json, input_json, status,
attempts, available_at, locked_by, lease_until, created_at)` with status `queued`, `running`,
`waiting`, `done`, `failed`.

`EVT-11` Agent workers claim with `FOR UPDATE SKIP LOCKED` and set a 120 s lease, renewed every
30 s while running. A job whose lease expired is reclaimable (crash recovery).

`EVT-12` At most one `running` job per `(case_id, agent_kind)` (partial unique index). A second
trigger for the same pair is queued and runs after the first.

`EVT-13` The **event-driven supervisor** is the set of subscribers in `modules/agents` that map
events to jobs:

| Event | Job for |
|---|---|
| `cases.case_opened`, `cases.patient_message_received` | `patient_companion` |
| `care.dose_missed`, `cases.red_flag_raised`, guardian check-in timers | `guardian_agent` |
| `booking.appointment_booked`, `reports.report_extracted`, `imaging.screening_completed`, `record.resource_written` | `doctor_assistant` (if the case has a booked doctor) |
| `agents.message_sent` addressed to an agent | that agent |
| `cases.doctor_decision_recorded` | `doctor_assistant` (resume) |
| `verifier.verdict_issued` with `revise` | the author agent |

## 6. Timers

`EVT-14` A durable timer is a row in `platform_timers`:
`(id, kind, due_at, ref_type, ref_id, payload_json, status, dedupe_key UNIQUE, fired_at)`.
The worker fires due timers (`due_at <= clock.now()`, `SKIP LOCKED`) by writing
`platform.timer_fired` to the outbox and marking the timer `fired`, in one transaction.

| Timer kind | Set by | Due | On fire |
|---|---|---|---|
| `dose_check` | care, for each scheduled dose | dose time + 60 min grace | if no `MedicationAdministration` recorded: write `not-done`, emit `care.dose_missed` |
| `approval_timeout` | cases, on `DoctorApprovalRequested` | + 4 h | `ApprovalTimedOut`: tell the patient, offer another doctor |
| `agent_request_deadline` | agents, on any `request` message | + per-intent deadline (default 30 min) | requester resumes with "no answer" path |
| `guardian_checkin_followup` | guardian agent | + 2 h | if patient did not answer: alert guardian |
| `slot_hold_expiry` | booking | + 5 min | release the hold |
| `verifier_hold_retry` | verifier | + 2 min (up to 15 tries) | retry model checks for held messages |

`EVT-15` Timers are cancelled by setting status `cancelled` (for example the approval timer when the
doctor answers). Firing a cancelled timer is a no-op.

## 7. The clock

`EVT-16` All code reads time through the `Clock` port. `datetime.now()` is forbidden outside
`platform/clock.py` (enforced by a ruff banned-API rule).

`EVT-17` `SimulatedClock` = real time + an offset stored in `platform_clock` (one row) so that every
process agrees. It can move only forward and only when `SUWAPATH_ENV` is `development` or `demo`:
`python main.py clock advance 4h` or `POST /api/v1/dev/clock/advance`. After an advance, the worker
is notified and fires due timers immediately.

`EVT-18` `SUWAPATH_ENV=production` binds `SystemClock` and the dev endpoints are not mounted.

## 8. LangGraph and the case log

`EVT-19` **The case event log is the single source of truth for facts.** LangGraph's checkpointer
(Postgres saver, schema `langgraph`) stores only execution position: which node, pending interrupt,
and scratch state of the current run. Thread ID is `"{case_id}:{agent_kind}"`.

`EVT-20` Graph nodes read case state fresh through `CaseService` at the start of every node that
makes a decision, and change the case only by appending events through `CaseService`
(optimistic locking with `expected_version`). On `VersionConflict`, the node reloads and decides
again (at most 3 times, then the job fails and retries).

`EVT-21` On resume after a pause (for example the doctor approval), the graph re-reads the case.
It never trusts state captured before the pause.
