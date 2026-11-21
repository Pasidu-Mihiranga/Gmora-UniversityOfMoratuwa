# 19. Customisable agents (proposal 4.5)

Every patient, guardian and doctor can shape how their own agent works: start from a template,
adjust settings, or describe a rule in their own words. SuwaPath turns each request into a typed
rule, shows it back for confirmation, and versions it. Customisation can only narrow access, safety
rules cannot be switched off, doctors approve every clinical reply, and every change is logged.

## 1. Principles

`CUS-1` **Settings, not prompts.** Customisation is a set of typed settings from a fixed catalogue
(section 3). User text is never appended to an agent's prompt as instructions. A request that does
not map to the catalogue gets "I can't set that. Here is what I can do:" with the closest options.
This keeps customisation from becoming a prompt-injection path.

`CUS-2` **Layers, most restrictive wins.**

| Layer | Set by | Examples | Can users change it? |
|---|---|---|---|
| L0 Safety | code | red flags, urgency floor, 1990 routing, verifier, doctor approval, emergency alerts reach the guardian unless the patient switched them off | never |
| L1 Platform bounds | platform admin | allowed range of every setting, default templates, per-role usage quotas | no |
| L2 Organization policy | hospital admin (doctors of that hospital) | mandatory reply footer, approval reminder interval | no |
| L3 Personal settings | the person | values inside the L1 and L2 bounds | yes |
| L4 Session choices | the person, for one case or session | "use my record" in a private consult | yes |

`CUS-3` **Narrow only for access.** Any setting that touches data access is checked by `SEC-14`: it
may reduce what others or agents see, never widen it beyond the patient's consent. Granting consent
is done only by the patient on the sharing screen, not through agent customisation.

`CUS-4` **Confirm, version, audit, notify.** Every change is shown back in plain words, confirmed,
stored as a new version with the previous one kept (one-tap revert), audited (`AUD-1`), and the
other affected person is told that something changed, without details they may not see ("Kamala
updated what she shares with you").

## 2. Templates

`CUS-5` Each role starts from a template, then adjusts. Templates are platform content (L1).

| Role | Templates |
|---|---|
| Patient | "Simple and gentle" (short replies, daily reminders), "Detailed" (full explanations), "Independent" (fewer check-ins) |
| Guardian | "Close watch" (alert after 1 missed dose, daily summary), "Balanced" (2 missed doses in 24 h, weekly summary), "Emergencies only" |
| Doctor | "Chronic care brief" (allergies, medicines, HbA1c and BP first), "Acute visit brief" (reason, findings, red flags first), "Minimal" |

## 3. Setting catalogue (with bounds)

### Patient
| Setting | Type | Bounds and default |
|---|---|---|
| Language | si, en | default from sign-up |
| Reply style | simple, detailed | simple |
| Display mode and text size | standard or simple; normal, large, extra large | simple and large for age 60+ (`FE-43`) |
| Dose reminder times | per slot | 05:00 to 23:00; default 08:00, 13:00, 19:00, 22:00 |
| Check-in frequency | daily, every 2 days, weekly, off | daily in elder care |
| Quiet hours | window | max 10 h; emergency and red-flag messages always pass |
| Who gets my emergency alert | approved guardians | default all approved guardians; patient can switch off (`SEC-10` rule 7) |
| Private consult history | none, 24 h, 7 days | none |
| Guardian suggestions | allow, ask me, off | ask me |

### Guardian (only for patients who granted them access)
| Setting | Type | Bounds and default |
|---|---|---|
| Missed-dose alert | count N in window W, optional slot | N 1 to 5, W 12 h to 7 days; template default |
| Summary | daily, weekly, off | weekly |
| Quiet hours | window | max 10 h; urgent and emergency alerts always pass |
| Alert channel | in-app, SMS | in-app |
| Escalation contact | another approved guardian of the same patient | optional |
| Suggest a change to the patient | proposal | the patient accepts or declines |

### Doctor
| Setting | Type | Bounds and default |
|---|---|---|
| Brief section order and pinned items | ordered list from the brief sections; up to 5 pinned observations (for example HbA1c) | template default; **alerts and clashes always first** |
| Queue secondary order | appointment time, waiting time | urgency is always the primary order |
| Reply templates | up to 20 text snippets | still drafted, verified and approved |
| Approval reminder | 30 min to 4 h | 1 h; the 4 h timeout is fixed |

## 4. Plain-language rules

`CUS-6` Flow (extends `SEC-15`):
1. The person types or says a rule in si or en.
2. Tier T2 (`KNW-3`) classifies it as `sharing_or_settings`; the gateway parses it with structured
   output into a `RuleProposal` that must validate against the catalogue and its bounds.
3. The proposal is checked against L0 to L2 and `SEC-14`. Anything locked or out of bounds is
   explained ("Emergency alerts always reach Nimal. You can turn this off only in Sharing.").
4. A rule card shows it back in plain words with an example: "Alert me if 2 evening doses are
   missed in 24 hours. Last week this would have alerted you once (Tuesday)."
5. Confirm, Edit or Cancel. Confirm saves a new version.

`CUS-7` Parsing uses the person's model budget (section 5). With the fake provider, a fixed set of
patterns per setting is parsed and the form is offered for everything else.

## 5. Limits (quotas) per person

`CUS-8` Usage quotas protect cost and prevent abuse. Defaults (L1, editable by the platform admin):

| Limit | Patient | Guardian | Doctor |
|---|---|---|---|
| Chat model turns (T3) per day | 60 | 30 | 200 |
| Model cost per day | USD 0.30 | USD 0.15 | USD 1.00 |
| Private consult turns per day | 40 | | |
| Rule or setting changes per day | 20 | 20 | 50 |
| Active plain-language rules | 20 | 20 | 30 |
| Uploads per day | 10 | | 50 |

`CUS-9` Rules, caches and direct tool calls (T0 to T2) do not count toward chat turns. Quotas reset
at midnight `Asia/Colombo` and are shown on the person's settings page.

`CUS-9a` **A quota never restricts access to care.** Always available regardless of quota: red-flag
screening and emergency guidance, 1990 routing, existing records within the person's permissions,
appointments and bookings, reminders, safety alerts, the doctor's approved replies, and handover to a
human. When a quota is reached the person is told in plain words which features still work.

`CUS-9b` Quotas are enforced **in the gateway**, atomically, so concurrent requests cannot exceed
them: before a call, a turn and the estimated cost are reserved with a single conditional update on
`ops_usage(user_id, day, turns, cost_usd)` (`UPDATE ... SET turns = turns + 1, cost_usd = cost_usd +
:est WHERE turns < :limit AND cost_usd + :est <= :cost_limit RETURNING ...`); after the call the cost
is reconciled with the actual value. Changes to quota defaults by platform admins are audited.

## 6. How agents use settings

`CUS-10` Settings compile into structured configuration read by agents and tools through
`ConsentService` and `CareService` ports (for example the doctor assistant reads `brief_order`; the
guardian agent reads alert thresholds through `AlertRule`). Agents never read raw rule text.

## 7. UI and UX

`CUS-11` **Controls first, typing optional.** Most people prefer tapping to writing rules. One
"My assistant" page per role:
1. **Templates** as large cards at the top, current one marked.
2. **Settings** grouped by plain questions ("When should we remind you?", "When should we tell
   Nimal?"), using chips, steppers, toggles and time pickers that cannot go out of bounds. This is
   the primary way to customise.
3. **Locked items** shown with a lock icon and one line saying why.
4. **Preview** of the effect on last week where possible, shown before saving.
5. **History** of versions with one-tap revert.
6. **"Prefer to type?"** a collapsed, optional box (si or en) that produces the same rule card
   (section 4) and fills in the same controls.

`CUS-12` Patient and guardian screens show at most 6 settings at once with "More settings" for the
rest; large controls (`FE-20`). Guardian suggestions appear to the patient as a card
("Nimal suggests: remind you at 20:00 instead of 19:00. Accept?").

`CUS-13` Doctor settings are compact: drag to reorder brief sections, pin observations, manage reply
templates.

`CUS-14` Platform admin sees settings bounds, templates, quotas and aggregate usage (how many people
use each template, how often quotas are hit), never personal content.

`CUS-15` **The backend is authoritative.** The settings catalogue, bounds, layers and quotas are
enforced by the backend on every write and every use. Frontend controls only make valid input easy;
they are not a security boundary.

`CUS-16` **Guardian suggestions** are stored as `customisation_suggestions(id, patient_id,
guardian_id, setting_key, proposed_value, status)`. The patient sees who suggested what and the
before and after values, and accepts or declines. Acceptance goes through the same validation,
bounds, versioning and audit as the patient's own change. Patients can switch suggestions off.

## 8. Storage

`CUS-17` Owned by the `consent` module (consent and preferences): `customisation_settings(owner_id,
role, setting_key, value, version, updated_at)`, `customisation_versions` (full history),
`customisation_suggestions`. Quota counters `ops_usage` are owned by `ops`.
