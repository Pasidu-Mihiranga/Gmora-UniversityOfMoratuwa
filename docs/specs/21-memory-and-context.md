# 21. Memory and context

Memory must never become a second, less governed patient record. Everything an agent "remembers"
lives in a governed store behind a port, belongs to one person, and is read through the
`AccessPolicy`.

## 1. Memory types and where they live

| Type | Meaning | Store | Priority |
|---|---|---|---|
| Working | the current conversation and execution position | case state + last turns (`cases`); LangGraph checkpoint holds position only (`EVT-19`) | P0 (exists) |
| Episodic | past episodes of care | case event log (exists) + **case summaries** (`memory`, this spec) | P0 |
| Semantic, about the person | conditions, medicines, allergies, results | FHIR record (`record`) | P0 (exists) |
| Semantic, learned in conversation | "lives alone", "home glucose meter at hand" | **confirmed facts** (`memory`) | P1 |
| Semantic, general | health knowledge | knowledge corpus (`knowledge`) | P0 (exists) |
| Procedural | how to act for this person | versioned prompts; typed settings and rules (spec 19) | P0 (exists) |
| Preferences | language, reply style, reminder times, preferred doctor | settings (spec 19); **preference suggestions** (`memory`) | P0 settings, P1 suggestions |

`MEM-1` **No patient data in LangGraph's long-term store.** LangGraph is used for execution state
only. Long-term memory goes through the `MemoryService` port, which applies encryption (`SEC-22`),
consent (`SEC-11`) and audit (`AUD-1`) on every read and write.

## 2. Memory items

`MEM-2` Every item in `memory_items` has: `owner_id` (the person it is about or belongs to),
`subject_patient_id` (when it concerns a patient), `kind` (`case_summary`, `learned_fact`,
`preference_suggestion`), `category` (a `DataCategory`, for consent), `purpose`, `source` (event,
communication or resource references), `content` (encrypted), `status`, `created_at`,
`confirmed_at`, `expires_at`, `version`.

`MEM-3` **Isolation.** An agent receives only items its principal may read for the current purpose
(`SEC-10` rule 5). A guardian's items (their alert history, their acknowledgements) belong to the
guardian; a guardian never reaches a patient's conversations through memory.

## 3. Case summaries (episodic, P0)

`MEM-4` When a case closes, a subscriber on the case close builds a summary **deterministically from
the case events**, with a template, no model: reason for contact (patient-reported concepts),
urgency and red flags, specialty and booking, missed-dose context, doctor reply approved (yes or no),
outcome. Each line keeps references to the events it came from.

`MEM-5` Summaries are derived records: encrypted with the patient's key, labelled in every UI and
prompt as "Summary by SuwaPath, not a medical record", never written into clinical resources
(`Condition`, `Observation`, ...), and never presented as clinician-authored. Category:
`conversations` for consent purposes.

`MEM-6` Use: the companion may mention a recent similar episode ("Last week you also felt dizzy");
the doctor brief lists recent episodes with links to their source events; guardians see only what
their grants allow.

## 4. Learned facts and preference suggestions (P1)

`MEM-7` An agent's inference alone is never stored. A candidate fact or preference is shown to the
person as a confirmation chip (`AGT-36`): "Should I remember that you live alone?",
"You usually book mornings. Make morning the default?". Only on confirmation is it stored, with the
person's confirmed wording or normalized value, its source turn and `confirmed_at`. Confirmed
preferences become spec 19 settings (versioned, revertible); confirmed facts become `learned_fact`
items shown in the person's record view under "Remembered from your conversations".

## 5. Context builder (P0)

`MEM-8` `ContextBuilder` assembles the context for each agent step from ports only (agents never
read memory storage directly), deterministically, within a fixed token budget per section:

| Order | Section | Default budget | Truncation |
|---|---|---|---|
| 1 | Safety: red flags, urgency floor, emergency state | unbounded | **never truncated** |
| 2 | Consent: what the recipient may see, current restrictions | unbounded | **never truncated** |
| 3 | Working: case state, open questions, last turns | 1,500 tokens | oldest turns dropped first |
| 4 | Semantic: record facts for the purpose (allergies and active medicines first) | 1,200 | allergies and active medicines never dropped; older results dropped first |
| 5 | Episodic: up to 3 recent relevant case summaries (same concepts or specialty, then recency) | 600 | least relevant dropped first |
| 6 | Procedural: settings that change behaviour (language, reply style, brief order) | 200 | never dropped |
| 7 | Knowledge: retrieved chunks with citations | 1,500 | lowest-ranked dropped first |

`MEM-9` The builder records which items it included (IDs only) on the agent step, so every answer
can be traced to the context it was given. Budgets are configuration and tuned with the latency and
quality suites.

## 6. Retention and deletion

`MEM-10` Design defaults, **pending the PDPA duty mapping** (issue X02), not legal periods: case
conversation text is kept for 90 days after the case closes, then removed, keeping the case summary;
case summaries are kept with the record; learned facts and preferences are kept until the person
removes them; private consult per spec 18.

`MEM-11` **Deletion propagates:** deleting an item removes it from `memory_items`, invalidates user
cache entries whose fingerprint includes it (`KNW-34`), and it is never used in context again. The
audit log keeps the fact that a deletion happened, not the content. Items in backups age out with
the backup cycle; crypto-shredding of the patient key removes everything (`SEC-25`). Where a record
must be kept for a documented reason (for example medical-record retention rules confirmed by the
PDPA mapping), the UI says so and why, instead of offering deletion.

`MEM-12` A full "my data" page (everything stored about a person, export and deletion requests) is
P2; access is already covered by the FHIR export, and each kind of memory is visible where it is used
(case history, record view, settings).
