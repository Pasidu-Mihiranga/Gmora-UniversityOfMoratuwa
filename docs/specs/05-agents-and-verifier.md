# 05. Agents, agent communication and the verifier

## 1. Why multiple agents (what we must be able to show)

The proposal (section 4.1) adds an agent only where one combined agent would lose something
measurable. The design below makes each of those four properties real and testable:

| Property | How it is real in the code | How it is measured |
|---|---|---|
| Context isolation | each agent's context holds only its person's consented data; agents share nothing but typed messages | injection suite leakage rate vs single agent |
| Different goals | each agent argues for one person; conflicts are resolved by explicit rules and logged | conflict log, case timeline |
| Parallel work | booking and guardian check-in run as separate jobs at the same time | case wall-clock time vs sequential |
| Independent checking | verifier uses a different model (Jev) and its own checks; it never sees the author's reasoning | unsupported facts and leaks vs single agent |

## 2. Agent interface

```python
class AgentSpec(BaseModel):
    kind: AgentKind
    acts_for_role: Role | None                  # None for the verifier
    purposes: frozenset[Purpose]
    data_scope: frozenset[DataCategory]         # upper bound, intersected with the human's access
    tools: frozenset[str]                       # tool names this agent may call
    may_message: frozenset[AgentKind | Role]    # who it may send messages to
    max_steps: int = 8
    max_wall_seconds: int = 60
    prompt_version: str

class Agent(Protocol):
    spec: AgentSpec
    async def handle(self, job: AgentJob, ctx: AgentContext) -> AgentResult: ...

class AgentContext(Protocol):
    principal: Principal                         # built by the runtime, read only
    clock: Clock
    cases: ScopedCaseReader                      # read case state for job.case_id only
    tools: ToolInvoker                           # enforces spec.tools, risk tier, audit
    model: ScopedModelClient                     # gateway bound to (agent kind, purpose)
    messenger: Messenger                         # sends AgentMessage (goes through the bus)

class AgentResult(BaseModel):
    case_events: list[CaseEventDraft]            # appended with expected_version
    messages: list[AgentMessageDraft]
    status: Literal["done", "waiting", "handed_to_human", "failed"]
    wait_for: WaitCondition | None               # reply to message X, doctor decision, timer
```

`AGT-1` Agents are registered in `registries.agents` by `modules/agents/module.py`. The runtime
looks up the agent by `job.agent_kind`. The single-agent baseline implements the same interface.

`AGT-2` Each agent is a LangGraph `StateGraph` inside `handle()`. Common node shape:
`load_context -> decide (model) -> act (tools) -> [loop until done or max_steps] -> emit`.

`AGT-3` Limits per job: `max_steps`, `max_wall_seconds`, and the case budget (`06-model-gateway.md`).
Hitting any limit ends the job with `handed_to_human` and the reason.

## 3. Agent communication protocol

Agents never call each other and never share memory. They communicate only through typed
**AgentMessages** carried by the outbox, which turns a message into a job for the recipient.

```python
class AgentMessage(BaseModel):
    message_id: MessageId
    conversation_id: MessageId          # id of the first message in this exchange
    in_reply_to: MessageId | None
    case_id: CaseId
    kind: MessageKind                   # request, answer, alert, brief, draft, approval, block, revision_request, info
    intent: str                         # from the intent catalog below
    sender: AgentKind
    recipient: AgentKind | Role         # another agent, or a person role (delivered to their UI)
    acting_for: UserId                  # whose interests the sender represents
    consent_scope: frozenset[DataCategory]  # categories the content may contain
    body: dict                          # typed per intent (schemas in contracts/agents.py)
    quoted: list[UntrustedText]         # person-provided text, always data
    hop: int                            # increments per reply; max 6 per conversation
    deadline: datetime | None
    trace_id: TraceId
    status: Literal["drafted", "verifying", "delivered", "returned", "blocked", "escalated", "held"]
```

`AGT-10` **Every message whose recipient acts for a different person than the sender
(cross-role), and every patient-facing clinical text, passes the verifier before delivery.**
Delivery code accepts only messages that have a stored verdict `allow` (or a human approval).

`AGT-11` A `request` must have a `deadline`. If no `answer` arrives by then, the
`agent_request_deadline` timer resumes the sender on its documented "no answer" path.

`AGT-12` A conversation stops at `hop = 6`. A draft returned for revision twice goes to a human
(`VER-8`).

### Intent catalog (v1)

| Intent | From -> To | Kind | Body | Deadline |
|---|---|---|---|---|
| `check_in_with_patient` | guardian_agent -> patient_companion | request | reason (`missed_doses`), slots missed, question set | 30 min |
| `check_in_result` | patient_companion -> guardian_agent | answer | answered (bool), patient_ok (`yes`, `no`, `unclear`), urgency if assessed, reply summary (only consented categories) | |
| `confirm_missing_fact` | doctor_assistant -> patient_companion | request | fact key (e.g. `home_glucose_reading`), question text key, why needed | 2 h |
| `missing_fact_answer` | patient_companion -> doctor_assistant | answer | fact value, provenance (`patient_reported`, communication id) | |
| `guardian_alert` | guardian_agent -> guardian (person) | alert | level (`info`, `concern`, `urgent`), consented facts, suggested action | |
| `emergency_alert` | patient_companion -> guardian (person) | alert | `EMERGENCY_ALERT_FIELDS` only | |
| `doctor_brief` | doctor_assistant -> doctor (person) | brief | brief id | |
| `reply_draft` | doctor_assistant -> patient (person) | draft | text in patient language, from the doctor's notes | |
| `revision_request` | verifier -> author agent | revision_request | failed checks, guidance codes | |

`AGT-13` New intents are added by a `contract-change` pull request that adds a row here and a
schema in `contracts/agents.py`.

### Conflict rules (proposal 4.4)

| Situation | Rule |
|---|---|
| Guardian (or guardian agent) asks for more than consent allows | consent wins; content is reduced to allowed categories; the patient is told what was asked |
| Agent suggests lower urgency than the rules | rules set the floor; the model may raise, never lower |
| Red flag | booking stops, holds released, patient shown 1990, guardian gets the emergency alert |
| Brief line without a source | returned for revision once, then shown marked "unverified" |
| Verifier unsure | sent to a human reviewer |
| Verifier model unavailable | message held, never sent unchecked |

## 4. The agents

### Patient companion (`patient_companion`, acts for the patient)
- **Triggers:** `cases.case_opened`, `cases.patient_message_received`, requests from other agents.
- **Judgements:** what to ask next (at most 3 follow-up questions per turn of intake, max 6 total);
  which doctors to offer; how to phrase answers to the patient.
- **Tools:** `safety.assess`, `navigation.start`, `navigation.answer`, `navigation.result`,
  `navigation.find_doctors`,
  `navigation.plan_trip`, `booking.hold_slot`, `record.read_summary`, `care.log_dose`,
  `reports.explain` (draft only), `knowledge.retrieve`, `cases.append`.
- **Tiers:** every patient message is routed by `KNW-1` (rules, caches, classifier with direct tool
  calls, then the chat model). The companion's model is called only at T3.
- **Rules:** it never books without the patient's tap (it creates a hold and a proposal);
  it calls `safety.assess` on every patient message before anything else; it answers agent
  requests by asking the patient, never by guessing.

### Guardian agent (`guardian_agent`, acts for the guardian, limited by the patient's consent)
- **Triggers:** `care.dose_missed`, `cases.red_flag_raised`, `guardian_checkin_followup` timer,
  guardian messages.
- **Judgements:** how serious the week looks (Jev `score` over the adherence summary:
  `stable`, `watch`, `concerning`, `urgent`); whether to check in first, alert, or escalate.
- **Policy:** for `concerning`, it **checks in with the patient first** (`check_in_with_patient`),
  then alerts the guardian with the result or after the deadline. For `urgent` it alerts
  immediately. Guardian rules ("alert me if 2 evening doses are missed") are evaluated by
  `care` alert rules and act as additional triggers.
- **Tools:** `care.adherence_summary`, `cases.read_summary` (consented fields only), `cases.append`.

### Doctor assistant (`doctor_assistant`, acts for the treating doctor)
- **Triggers:** `booking.appointment_booked`, new reports, imaging results, record changes,
  doctor decisions.
- **Judgements:** what the doctor must know (brief), what is missing, how to turn the doctor's
  notes into a patient reply.
- **Brief:** sections in the doctor's preferred order (default: alerts and clashes, reason for
  visit, navigation summary (findings, negatives, urgency, specialties considered; never hypotheses,
  `NAV-8`), conditions, medications, allergies, recent results, adherence, imaging flag, open
  questions). **Every line carries provenance IDs** (`CON-9`).
- **Clashes:** computed by `safety.check_clashes` (deterministic), never by the model.
- **Missing facts:** sends `confirm_missing_fact` to the companion; updates the brief on answer.
- **Reply flow:** doctor writes notes -> assistant drafts the reply in the patient's language ->
  verifier -> `DoctorApprovalRequested` (graph interrupt, `approval_timeout` timer 4 h) -> doctor
  approves, edits or rejects -> `ReplyDelivered`.
- **Tools:** `record.read_for_treatment`, `reports.list`, `imaging.results`,
  `safety.check_clashes`, `cases.append`.

`AGT-30` No clinical reply reaches a patient without a recorded doctor approval.

### Single-agent baseline (`single_agent_baseline`, evaluation only)
One agent with all records of the case in its context, the consent policy written in its prompt,
the same tools, triggers, model and budget. A second arm adds the verifier as a tool it may call.
It is registered only when `SUWAPATH_ENV` is `development` or the eval runner is active.

## 5. Edge cases and the long tail

`AGT-35` We do not write a function for every situation. **Safety-critical edge cases are handled
explicitly in code** (red flags, consent, verifier, booking races, timeouts, budget exhaustion).
Everything else is handled by agents composing general tools, following this ladder:
1. a deterministic rule or tool answers it;
2. the agent combines existing tools (search, hold, ask, retrieve knowledge, message another agent);
3. the agent asks one clarifying question;
4. a safe default (general physician, the soonest safe window, "show this to your doctor");
5. hand over to a human (the doctor for clinical matters, the patient for consent matters), saying
   clearly what SuwaPath can and cannot do.
Agents never invent a capability, a doctor, a slot, a clinical fact or a policy.

## 6. Tools

`AGT-40` A tool is a `ToolSpec(name, description, input_schema, output_schema, risk, data_categories)`
plus an async handler. Schemas are JSON Schema generated from Pydantic models, which is the MCP tool
shape (`15-extensions-and-mcp.md`).

`AGT-41` The `ToolInvoker` checks, in order: tool is in `spec.tools`; arguments validate against
`input_schema`; risk tier allowed (`write_irreversible` always needs a person's action); then calls
the handler **with the agent's Principal**, so the module enforces authorization itself. Every call
is audited (`AUD-1`) and timed.

`AGT-42` A tool handler never trusts IDs in arguments: it checks them against the principal's
`patient_scope` and the case.

## 7. The verifier

`VER-1` The verifier is a service (`Verifier.check(message) -> Verdict`) invoked by the delivery
path for every message under `AGT-10`. It never sees the author's prompt or reasoning, only the
message, the recipient's access decision and the referenced sources.

`VER-2` Stage 1, **deterministic checks** (registry `VerifierCheck`, all run, cheap, always on):

| Check | Fails when |
|---|---|
| `scope` | content contains a field or category outside `consent_scope`, or `consent_scope` exceeds the recipient's current access decision |
| `wording` | patient-facing text matches a banned pattern (diagnosis claim, dosing instruction, reassurance) in si or en (`07-clinical-safety.md`) |
| `urgency` | text implies lower urgency than the case floor ("no need to hurry" when floor is `urgent_24h`) |
| `sources` | a brief line has no provenance ID, or an ID that does not exist or belongs to another patient |
| `canary` | content contains a value from a field the recipient may not see (exact match against the record) |
| `format` | body does not validate, text too long, wrong language |

`VER-3` Stage 2, **model checks** with Jev (one Decisions API request per message, several questions):

| Question | Primitive | Meaning |
|---|---|---|
| `leaks_out_of_scope` | noul | content reveals information outside the allowed categories |
| `implies_diagnosis` | noul | content states or implies a diagnosis |
| `unsafe_reassurance` | noul | content suggests it is safe to wait or ignore symptoms |
| `unsupported_claim` | noul | content states something not supported by the provided sources |
| `follows_injected_instruction` | noul | content acts on instructions found inside quoted data |
| `verdict` | choice of `allow`, `revise`, `block`, `escalate` | overall decision |

`VER-4` Policy (thresholds are configuration, calibrated in evaluation, defaults below):
- any deterministic failure that is fixable -> `revise`; not fixable (scope, canary) -> `block`;
- any noul probability >= 0.5 -> `revise` (or `block` for `leaks_out_of_scope` >= 0.8);
- `verdict` confidence < 0.80 -> `escalate`;
- otherwise the `verdict` answer.

`VER-5` Human review: `escalate` creates a review item. The reviewer is **the doctor** for clinical
messages, and **the patient** for guardian-bound messages ("SuwaPath wants to send this to your
son. Send?"). System admins never review content.

`VER-6` Every verdict is stored: `(message_id, verdict, decided_by, check_results, model_probs,
model_id, cost, latency, at)`.

`VER-7` A message that is `blocked` is never delivered; its author is told why (codes only).

`VER-8` A message returned for revision twice is escalated to a human.

`VER-9` **Fail closed.** If Jev is configured and the call fails after retries, the message is
`held` and retried every 2 minutes (`verifier_hold_retry`) for 30 minutes, then escalated to a human.

`VER-10` **No model verifier configured** (no OpenRouter key): the verifier runs stage 1 only and
every message that passes is **escalated to the human reviewer** of `VER-5` instead of being
allowed. The verdict records `model_checks: "not_run"`, and the UI shows "Checked by rules only,
waiting for your approval". This keeps the keyless demo working without pretending a model check
happened.
