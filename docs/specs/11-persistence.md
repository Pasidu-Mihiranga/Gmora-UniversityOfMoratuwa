# 11. Persistence

## 1. Database

`DB-1` PostgreSQL 16 with extensions `pg_trgm` and `pgcrypto` (for `gen_random_bytes` in tests
only). SQLAlchemy 2.0 async with the `psycopg` (v3) driver. LangGraph's Postgres checkpointer uses
the same database, schema `langgraph`.

`DB-2` Every module owns its tables. Table names are prefixed with the module name
(`booking_slots`). A module never reads or writes another module's tables (`ARC-9`); it uses ports.

`DB-3` Primary keys are UUIDv7 (`uuid` type). Timestamps are `timestamptz`. Every table that holds
patient data has a non-null `patient_id` column (partition key, `ARC-14`).

`DB-4` Encrypted columns are `bytea` and use the `EncryptedString` / `EncryptedJSON` column types
from `platform.crypto` (`SEC-22`).

## 2. Unit of work and transactions

`DB-5` `UnitOfWork` opens one transaction and exposes: the session, `outbox.add(event)`,
`audit.add(entry)`, `timers.schedule(...)`, `timers.cancel(...)`. `commit()` writes them all and
sends `NOTIFY suwapath_outbox` after commit.

`DB-6` Optimistic locking: the case aggregate appends with `expected_version`; the unique constraint
`(case_id, version)` turns a race into `VersionConflict`.

`DB-7` Isolation level `READ COMMITTED`. Correctness under concurrency comes from unique constraints,
`SKIP LOCKED` and versions, not from `SERIALIZABLE`.

## 3. Tables (initial schema)

| Module | Tables |
|---|---|
| platform | `platform_outbox`, `platform_inbox`, `platform_timers`, `platform_clock`, `platform_flags`, `platform_idempotency_keys` |
| identity | `identity_users`, `identity_refresh_tokens`, `identity_login_attempts` |
| consent | `consent_grants`, `consent_rules`, `consent_rule_versions` |
| record | `record_patient_keys`, `record_patients`, `record_practitioners`, `record_conditions`, `record_allergies`, `record_medication_statements`, `record_observations`, `record_provenance`, `record_documents` |
| booking | `booking_organizations`, `booking_practitioner_roles`, `booking_schedules`, `booking_slots`, `booking_holds`, `booking_appointments` |
| care | `care_programmes`, `care_enrollments`, `care_medication_administrations`, `care_alert_rules` |
| reports | `reports_reports`, `reports_extractions` |
| imaging | `imaging_studies`, `imaging_results` |
| cases | `cases_cases`, `cases_events`, `cases_timeline`, `cases_communications` |
| agents | `agents_jobs`, `agents_messages` |
| verifier | `verifier_verdicts`, `verifier_reviews` |
| notifications | `notifications_items` |
| ops | `ops_model_calls`, `ops_budgets` |
| audit | `audit_log` |

Column definitions are the SQLAlchemy models in each module's `adapters/models.py`, created in
Wave 0 from the contracts in `02-contracts-and-domain.md`.

## 4. Indexes (required)

| Table | Index | Serves |
|---|---|---|
| all patient tables | `(patient_id, created_at DESC)` | per-patient lists |
| `platform_outbox` | `(available_at) WHERE status = 'pending'` | dispatcher |
| `platform_timers` | `(due_at) WHERE status = 'scheduled'`; unique `dedupe_key` | timer scan |
| `platform_inbox` | unique `(event_id, subscriber)` | idempotency |
| `agents_jobs` | `(available_at) WHERE status = 'queued'`; unique `(case_id, agent_kind) WHERE status = 'running'` | claiming |
| `cases_events` | unique `(case_id, version)` | optimistic lock and replay |
| `cases_timeline` | `(case_id, at DESC)` | timeline pages |
| `booking_slots` | `(practitioner_role_id, starts_at)`, `(starts_at) WHERE status = 'free'` | slot search |
| `booking_appointments` | unique `(slot_id) WHERE status IN ('held','booked')`; `(patient_id, starts_at)` | no double booking |
| `booking_organizations` | `(lat, lng)` | bounding box |
| `record_practitioners` | GIN trigram on `search_name` | doctor name search |
| `booking_practitioner_roles` | `(specialty)` | filter |
| `consent_grants` | `(patient_id, grantee_id) WHERE status = 'active'` | access decisions |
| `audit_log` | `(patient_id, at DESC)`, `(actor_id, at DESC)`, `(trace_id)`, BRIN `(at)` | audit search |
| `ops_model_calls` | `(case_id)`, BRIN `(at)` | ops page |
| `notifications_items` | `(user_id, created_at DESC) WHERE read_at IS NULL` | unread badge |

`DB-8` Any new query used by an endpoint that lists or searches must be backed by an index named in
this table or added by its pull request. Reviewers check `EXPLAIN` for new list queries.

## 5. Migrations

`DB-9` Alembic with one linear history. Wave 0 creates `0001_initial` containing every table and
index above. Later changes add new migration files. If two pull requests create migrations with the
same parent, the second rebases and sets its `down_revision` to the first (no merge migrations).

`DB-10` CI runs `alembic upgrade head` on an empty database and `alembic check` (models and
migrations in sync).

## 6. Files

`DB-11` `FileStore` port: `put(patient_id, bytes, content_type) -> DocumentId`, `get(document_id)`.
`LocalFileStore` writes encrypted blobs to `project/storage/` (git-ignored). An S3 adapter is a
later swap.

## 7. Seed and reset

`DB-12` `python main.py seed --seed 42` loads the synthetic world (`16-demo-and-seed.md`) through
module ports, never by writing tables directly. `POST /dev/scenario/reset` truncates and reseeds in
development and demo only.
