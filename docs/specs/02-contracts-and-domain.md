# 02. Contracts and domain model

Everything in this file lives in `project/suwapath/contracts/` as Pydantic v2 models, enums and
`Protocol`s. It is frozen at the end of Wave 0. Changes follow the `contract-change` process.

## 1. Conventions

`CON-1` All IDs are UUIDv7 (time-ordered, good index locality), typed with `NewType`:
`UserId`, `PatientId`, `PractitionerId`, `OrganizationId`, `CaseId`, `EventId`, `MessageId`,
`JobId`, `ResourceId`, `ConsentId`, `AppointmentId`, `SlotId`, `DocumentId`, `TraceId`.
Generated only by `platform.ids.new_id()`.

`CON-2` All timestamps are timezone-aware UTC (`datetime` with `tzinfo=UTC`), produced only by the
`Clock` port. Display converts to `Asia/Colombo`.

`CON-3` All contract models are immutable (`model_config = ConfigDict(frozen=True, extra="forbid")`).

`CON-4` Text a person typed, and text extracted from a document, is wrapped as
`UntrustedText(value: str, source: str)` everywhere it travels. Prompt builders render it inside a
quoted data block (`06-model-gateway.md`, `GW-20`).

## 2. Enums

| Enum | Values |
|---|---|
| `Role` | `patient`, `guardian`, `doctor`, `hospital_admin`, `system_admin` |
| `PrincipalKind` | `user`, `agent`, `system` |
| `AgentKind` | `patient_companion`, `guardian_agent`, `doctor_assistant`, `verifier`, `single_agent_baseline` |
| `Purpose` | `care_navigation`, `guardian_monitoring`, `doctor_brief`, `booking`, `verification`, `emergency`, `self_access`, `treatment`, `administration`, `evaluation` |
| `DataCategory` | `demographics`, `contact`, `conditions`, `allergies`, `medications`, `adherence`, `observations`, `reports`, `imaging`, `appointments`, `conversations`, `sensitive_sexual_health`, `sensitive_mental_health` |
| `Urgency` (ordered) | `routine` < `soon_72h` < `urgent_24h` < `emergency` |
| `Language` | `si`, `en` (`ta` reserved) |
| `CaseStatus` | `open`, `intake`, `assessed`, `emergency`, `in_care`, `awaiting_doctor`, `replied`, `handed_to_human`, `closed` |
| `MessageKind` | `request`, `answer`, `alert`, `brief`, `draft`, `approval`, `block`, `revision_request`, `info` |
| `VerdictKind` | `allow`, `revise`, `block`, `escalate`, `hold` |
| `ValidationStatus` | `unvalidated`, `validated` |
| `ToolRisk` | `read`, `write_reversible`, `write_irreversible` |

`CON-5` `Urgency` comparison uses its order. Helper `max_urgency(a, b)` is the only allowed way to
combine urgencies.

## 3. Principal

```python
class Principal(BaseModel):
    kind: PrincipalKind
    user_id: UserId | None          # set when kind == user
    agent_kind: AgentKind | None    # set when kind == agent
    role: Role | None               # role of the human (or of the person the agent acts for)
    acting_for: UserId | None       # the human an agent acts for; equals user_id for users
    patient_scope: PatientId | None # the patient whose data this request concerns, if any
    purpose: Purpose
    trace_id: TraceId
    session_id: str | None
```

`CON-6` A `Principal` is constructed only by:
- `platform.http.auth` from a verified access token (users), or
- the agent runtime from a job whose origin was validated when the job was created (agents), or
- `platform` for system tasks (`kind=system`, used for timers and migrations; it has no access to
  clinical fields).

Agents and models never construct or modify a `Principal`. Tool arguments never contain one.

## 4. FHIR-shaped resources

`CON-7` Resources follow FHIR R4 names, field names and references for the subset listed. They are
**FHIR-shaped, not a FHIR server**: we validate our own Pydantic models and provide a FHIR R4 JSON
export (`GET /patients/{id}/fhir/export`) that produces a `Bundle` of type `collection`.

Common fields on every resource: `id`, `resource_type`, `patient_id` (except directory resources),
`meta.version_id` (int), `meta.last_updated`, `meta.source` (module), `provenance_ids` (list).

| Resource | Owner module | Key fields | Category | Encrypted fields |
|---|---|---|---|---|
| `Patient` | record | name, birth_date, gender, language, telecom, address, nic, location (lat, lng, district), age_band | demographics, contact | name, birth_date, telecom, address, nic |
| `RelatedPerson` | consent | patient_id, user_id, relationship (`son`, `daughter`, `spouse`, `carer`, ...), lives_abroad | contact | name, telecom |
| `Practitioner` | record | name, qualifications, languages | directory | none |
| `PractitionerRole` | booking | practitioner_id, organization_id, specialty, fee_lkr | directory | none |
| `Organization` | booking | name, district, location, phone | directory | none |
| `Condition` | record | code (SNOMED CT or local), display, clinical_status, onset, recorded_by | conditions | display, note |
| `AllergyIntolerance` | record | substance code, display, reaction, criticality | allergies | display, reaction |
| `MedicationStatement` | record | medication code, display, dosage (text, timing: times of day, days), status, prescriber | medications | display, dosage.text |
| `MedicationAdministration` | care | medication_statement_id, scheduled_at, status (`completed`, `not-done`), recorded_at, recorded_by | adherence | note |
| `Observation` | record | code (LOINC where known), display, value, unit, reference_range, effective_at, derived_from (report) | observations | value, display |
| `DiagnosticReport` | reports | category (`lab`, `imaging`), document_id, conclusion_text, issued_at | reports, imaging | conclusion_text |
| `Appointment` | booking | slot_id, practitioner_role_id, status (`proposed`, `booked`, `cancelled`, `fulfilled`, `noshow`), case_id | appointments | none |
| `Slot`, `Schedule` | booking | session window, capacity, status | directory | none |
| `Consent` | consent | grantor, grantee, categories, purpose, period, status, version | n/a | none |
| `Provenance` | record | target refs, recorded_at, agent (who), activity (`entered`, `extracted`, `imported`, `inferred`), entity (document_id, page, line, quote_hash) | n/a | entity.quote |
| `Communication` | cases | sender, recipient, payload, in_response_to | conversations | payload |

`CON-8` `MedicationStatement` describes what is prescribed. `MedicationAdministration` is one actual
event: a dose taken (`completed`) or missed (`not-done` with reason `missed`). They are never mixed.

`CON-9` Every clinical fact that can appear in a doctor brief MUST have at least one `Provenance`
pointing to where it came from. Facts entered by seed data carry `activity="imported"` and
`entity.document_id` of the seeded source document.

## 5. The case aggregate

A **case** is one episode of care: from the patient's first message (or a guardian trigger) until it
is closed. Its state is rebuilt by folding its events in version order (`03-events-and-processes.md`).

```python
class CaseState(BaseModel):
    id: CaseId
    patient_id: PatientId
    status: CaseStatus
    urgency: Urgency | None
    urgency_floor: Urgency | None         # from rules; urgency >= floor always
    language: Language
    red_flags: list[RedFlagHit]
    facts: list[CaseFact]                 # each with provenance ids
    open_questions: list[OpenQuestion]
    suggested_route: RouteSuggestion | None
    appointments: list[AppointmentId]
    guardian_status: GuardianBranchState | None
    brief_id: ResourceId | None
    pending_approval: PendingApproval | None
    budget_used: BudgetUsage
    version: int
```

### Case state machine

```
open -> intake -> assessed -> in_care -> awaiting_doctor -> replied -> closed
             \-> emergency -> closed
any non-terminal -> handed_to_human -> closed
```

| From | Event | To | Guard |
|---|---|---|---|
| (none) | `CaseOpened` | `open` | patient has an active account |
| `open` | `IntakeStarted` | `intake` | |
| `intake` | `UrgencyAssessed` | `assessed` | urgency >= floor |
| any active | `RedFlagRaised` | `emergency` | always wins; cancels pending holds |
| `assessed` | `AppointmentBooked` | `in_care` | |
| `in_care` | `DoctorApprovalRequested` | `awaiting_doctor` | a verified draft exists |
| `awaiting_doctor` | `ReplyDelivered` | `replied` | doctor approved |
| `awaiting_doctor` | `ApprovalTimedOut` | `in_care` | patient told and offered another doctor |
| any active | `HandedToHuman` | `handed_to_human` | budget exhausted, verifier unsure twice, or agent failure |
| `replied`, `emergency`, `handed_to_human` | `CaseClosed` | `closed` | |

`CON-10` An event whose transition is not in this table MUST be rejected with `InvalidTransition`
by the case aggregate, and the attempted change is logged.

## 6. Errors

All errors derive from `SuwaPathError(code: str, message: str, retryable: bool)`.

| Error | HTTP | Meaning |
|---|---|---|
| `NotFound` | 404 | resource does not exist **or** the principal may not know it exists |
| `AccessDenied` | 403 | role is not allowed this action |
| `ConsentDenied` | 403 | consent policy denies this data for this principal and purpose |
| `VersionConflict` | 409 | optimistic lock failed; caller reloads and retries |
| `InvalidTransition` | 409 | case state machine rejected the event |
| `ValidationFailed` | 422 | input invalid |
| `BudgetExceeded` | 429 | case, agent or daily budget exhausted |
| `ProviderUnavailable` | 503 | model provider failed after retries |
| `CapabilityMissing` | 501 | configured provider cannot do what was requested |
| `SafetyStop` | 409 | a safety rule stopped the action (for example booking during a red flag) |
| `PhiEgressBlocked` | 500 | outbound inspector found an identifier; call not sent |

`CON-11` `NotFound` is returned instead of `ConsentDenied` when revealing existence would leak
information (for example another patient's case ID).
