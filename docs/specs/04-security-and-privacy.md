# 04. Security and privacy

Privacy is the first design constraint. When a feature and a privacy rule conflict, the privacy rule
wins and the feature changes.

## 1. Identity and sessions (module `identity`)

`SEC-1` Passwords are hashed with Argon2id (`argon2-cffi` defaults). Login is rate limited per
account and per IP (5 failures in 10 minutes, then 10 minute lockout).

`SEC-2` Access token: JWT, HS256, 15 minutes, claims `sub`, `role`, `sid`, `iat`, `exp`, `jti`.
Secret from `JWT_SECRET` (startup fails if it is empty and `SUWAPATH_ENV=production`).

`SEC-3` Refresh token: 256-bit random opaque string, 7 days, stored only as SHA-256 hash with a
`family_id`. **Rotation:** every refresh issues a new token and revokes the old one. **Reuse
detection:** presenting a revoked token revokes the whole family. Logout revokes the family.

`SEC-4` The browser stores the refresh token in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie
scoped to `/api/v1/auth`. The access token lives in memory only (not `localStorage`).

`SEC-5` SSE cannot send headers, so streams use a **stream ticket**: `POST /api/v1/stream-tickets`
returns a random ticket valid for 60 s and one use, bound to the user and the requested stream.

`SEC-6` JWTs, refresh tokens, passwords and tickets are never logged.

## 2. Access control: two layers

**Layer 1, role-based (routes).** Each router declares allowed roles. Checked by the auth dependency
before the handler runs.

| Route group | Roles |
|---|---|
| `/patients/{id}/...` | patient (self), guardian (consented), doctor (treating) |
| `/guardian/...` | guardian |
| `/doctor/...` | doctor |
| `/hospital/...` | hospital_admin |
| `/ops/...`, `/audit/...` | system_admin |
| `/dev/...` | any authenticated user, and only when `SUWAPATH_ENV` is `development` or `demo` |

**Layer 2, attribute-based (data).** Every read or write of patient data goes through
`AccessPolicy.decide()`:

```python
class AccessRequest(BaseModel):
    principal: Principal
    action: Literal["read", "write"]
    patient_id: PatientId
    category: DataCategory
    resource_type: str
    purpose: Purpose

class AccessDecision(BaseModel):
    allowed: bool
    allowed_fields: frozenset[str] | None   # None means all fields of the resource
    reason: str                             # machine readable, e.g. "guardian_grant_missing"
    policy_version: str
```

`SEC-10` Policy rules, evaluated in order, **deny by default**:

1. `system_admin` and `hospital_admin` never read clinical categories. Hospital admins read
   `appointments` metadata of their own organization only.
2. A patient reads and writes their own record (`purpose=self_access`). Writes are limited to
   adherence (dose taken), preferences, consent and uploads.
3. A guardian reads only the categories in an active `Consent` grant from that patient, for
   purposes `guardian_monitoring` or `emergency`. `conversations` and sensitive categories are
   never granted by default.
4. A doctor reads a patient's record only while a **treating relationship** exists: an
   `Appointment` with that doctor in status `booked` or `fulfilled`, from booking time until 30 days
   after the appointment. Sensitive categories are hidden unless the patient switched them on for
   that doctor.
5. An agent principal gets the **intersection** of (what its `acting_for` human may access for the
   purpose) and (the agent's declared data scope in its `AgentSpec`). It can never exceed its human.
6. `system` principals read no clinical fields.
7. `emergency` purpose: the guardian receives the minimal emergency alert content
   (`EMERGENCY_ALERT_FIELDS`: patient first name, the fact that urgent help is advised, how to
   contact the patient). This default is on and the patient can switch it off.

`SEC-11` The record module calls `AccessPolicy.decide()` **inside its data-access layer**, for
every query, and returns only `allowed_fields`. API handlers and agents cannot bypass it because the
record port takes a `Principal` on every method.

`SEC-12` **No caching of decisions across requests.** A decision may be memoized only inside one
request or one agent step. Revocation therefore takes effect on the next request.

`SEC-13` Background deliveries (alerts, messages) re-evaluate access **at delivery time**, not at
creation time. If consent was revoked in between, the message is dropped and audited.

`SEC-14` Customisation can only **narrow** access. A proposed rule is validated by computing the
access it would grant and rejecting it if any (grantee, category, purpose) is wider than the current
grants. Safety rules (`07-clinical-safety.md`) cannot be switched off by any rule.

### Plain-language rules

`SEC-15` A patient, guardian or doctor can type a rule ("Share my medicines with my son, not my
chats"). The flow is: parse with the gateway into a typed `RuleProposal` (structured output) ->
validate (`SEC-14`) -> show back in plain words in the person's language -> person confirms ->
stored as a new version -> `consent.rule_confirmed`. With the offline provider, parsing uses a small
deterministic pattern set and the UI also offers a form.

## 3. Encryption at rest (envelope encryption)

`SEC-20` Each patient has one **data encryption key (DEK)**, 256-bit, created with the patient.
The DEK is stored only **wrapped** by a key-encryption key (KEK) in `record_patient_keys`
`(patient_id, wrapped_dek, kek_id, created_at, destroyed_at)`.

`SEC-21` `KeyProvider` port: `wrap(dek) -> bytes`, `unwrap(wrapped, kek_id) -> bytes`.
`LocalKeyProvider` uses `SUWAPATH_MASTER_KEY` (base64, 32 bytes) with AES-256-GCM.
`AwsKmsKeyProvider` is a later adapter behind the same port.

`SEC-22` Field encryption uses AES-256-GCM with a fresh 96-bit random nonce per value and
**associated data** `"{table}.{column}:{row_id}:{patient_id}"`, which stops a ciphertext from being
copied to another row or patient. Stored format: `version(1 byte) | kek_id_len | kek_id | nonce(12) |
ciphertext+tag`.

`SEC-23` Which fields are encrypted is declared per resource in `02-contracts-and-domain.md`
(column "Encrypted fields"). IDs, foreign keys, timestamps, statuses, categories and codes needed
for queries stay plain.

`SEC-24` Unwrapped DEKs are cached in process memory for at most 5 minutes (LRU, 1000 entries) and
never written to disk or logs.

`SEC-25` **Crypto-shredding:** destroying a patient's DEK (`destroyed_at` set, `wrapped_dek`
overwritten) makes their encrypted fields unreadable. The spec is honest about limits: backups taken
before destruction still contain the wrapped DEK until they expire, and in-memory caches clear
within 5 minutes.

`SEC-26` Uploaded files (reports, X-rays) are encrypted with the patient's DEK before being written
to the `FileStore`.

## 4. PHI minimization before any external model call

`SEC-30` Every external model call passes through this pipeline in the gateway, in this order:

1. **Purpose allowlist.** The caller declares `(agent_kind, purpose)`. A `PurposeProfile` lists the
   resource fields allowed in the prompt for that pair. Everything else is removed.
2. **Drop unneeded data.** Empty fields, internal IDs and metadata are dropped.
3. **Pseudonymize.** Identifiers known from structured fields (patient name and its parts, NIC,
   phone, address, names of related persons and doctors) are replaced with stable tokens such as
   `[PATIENT]`, `[PERSON_1]`, `[DOCTOR_1]`, `[PHONE_1]`. Replacement uses the known values from the
   record, in both Sinhala and Latin script where stored, not regex guessing.
4. **Inspect.** The final outbound payload is scanned for any known identifier value and for NIC
   (old: 9 digits + V/X, new: 12 digits), Sri Lankan phone numbers and email patterns. A hit blocks
   the call (`PhiEgressBlocked`), is audited, and the agent step fails safely.
5. **Send** to the provider.
6. **Restore.** Tokens are mapped back to real values only in the response handler, and only for
   values the principal is allowed to see.

`SEC-31` The pseudonym map lives only in memory for the duration of one call.

`SEC-32` Logs and traces record IDs, purposes, token counts, cost and latency. Never prompt text,
response text or field values. A logging filter drops any attribute not on an allowlist.

`SEC-33` Provider choice: Vertex AI (no training on customer data). On OpenRouter, requests set
provider preferences that exclude providers which retain or train on data.

## 5. Audit (module `audit`)

`AUD-1` Every one of these is audited: login and logout, every patient-data read and write (human
or agent), every access decision that denies, every tool call, every model call, every consent and
rule change, every verifier verdict, every doctor decision, every admin action, every dead-letter
replay.

`AUD-2` Entry: `(id, at, actor_kind, actor_id, agent_kind, acting_for, patient_id, action,
resource_type, resource_id, purpose, outcome (allow, deny, error), reason, trace_id, metadata_json,
prev_hash, hash)`. `metadata_json` holds no PHI.

`AUD-3` **Tamper evidence:** `hash = SHA-256(prev_hash || canonical_json(entry))`. A verification
command (`python main.py audit verify`) checks the chain.

`AUD-4` Append-only: a database trigger rejects every `UPDATE` and `DELETE` on `audit_log` and
`cases_events` (migration 0002). In production the application role also has no `TRUNCATE` on them;
`TRUNCATE` is used only by development and test resets.

`AUD-5` Writes that change state write their audit entry in the same transaction. Reads write
their audit entry synchronously before the data is returned.

`AUD-6` Indexes: `(patient_id, at DESC)`, `(actor_id, at DESC)`, `(trace_id)`, BRIN on `at`.

## 6. Threat model (summary)

| Threat | Control | Test |
|---|---|---|
| Prompt injection in patient text or reports | untrusted data blocks, verifier `follows_injected_instruction` check, tools enforce authorization themselves | injection suite (`13-evaluation.md`) |
| Guardian or doctor sees more than allowed | `AccessPolicy` in the data layer, field filtering, verifier scope check | property tests, IDOR suite |
| IDOR (guessing another patient's ID) | every route checks the principal against the patient; `NotFound` instead of `Forbidden` | IDOR suite over every route |
| Agent exceeds its person's rights | agent principal is an intersection (`SEC-10` rule 5); tool allowlists | unit tests per agent spec |
| Token theft | short access token in memory, rotating refresh in `HttpOnly` cookie, reuse detection | auth tests |
| PHI leaves to a provider | minimization pipeline and outbound inspector | egress tests with canary identifiers |
| Verifier bypass | delivery code accepts only messages with a stored `allow` verdict | integration test |
| Replay of events or timers | inbox idempotency, timer status checks | idempotency tests |

## 7. Secrets and data in git

`SEC-40` No credentials, keys, service-account files, `.env`, model weights or non-synthetic
patient data are ever committed. `.gitignore` covers them and CI runs a secret scan (`gitleaks`).
