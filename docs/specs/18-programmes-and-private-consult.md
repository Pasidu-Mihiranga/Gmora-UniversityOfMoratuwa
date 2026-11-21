# 18. Care programmes and the private consult

## 1. Programme catalogue, offerings, referrals and enrolment

Programmes connect every party: the platform defines them safely once, hospitals offer them,
doctors refer to them, patients join them, guardians follow them within consent.

| Role | Responsibility |
|---|---|
| Platform admin (`system_admin`) | owns the **catalogue**: programme types, clinical content, red flags, schedules, alert rules; enables or disables types; **approves** hospital offerings |
| Hospital admin | creates **offerings** of catalogue types: sessions, linked doctors, capacity, fee, location |
| Doctor | **refers** a patient to a programme (optionally to a specific offering) |
| Patient | accepts a referral or joins on their own; chooses an offering or none; sets guardian consent |

`PRG-1` A programme **type** is code: a `CareProgramme` registration (`CARE-1`) with versioned
content files in `modules/care/programmes/<key>/` (si and en text, schedule template, alert rules,
red-flag rule IDs that apply, knowledge corpus tags). Only the platform can add or change types,
through a pull request reviewed like any clinical content.

`PRG-2` `care_programme_types(key, enabled, content_version)` controls availability. Only enabled
types are visible anywhere.

`PRG-3` An **offering** is `care_offerings(id, programme_key, organization_id, title, description,
schedule_ids, practitioner_role_ids, capacity, fee_lkr, status)` with status `draft`,
`pending_approval`, `approved`, `rejected`, `archived`. Only `approved` offerings are visible to
patients and doctors. Hospital admins edit only their own organization's offerings.

`PRG-4` Offerings are listed by distance, then next available session. Listing fees, commissions
and booking fees never change the order (`NAV-3`).

`PRG-5` A **referral** `care_referrals(id, patient_id, programme_key, offering_id?, referred_by,
note_key, status)` is created by a treating doctor and appears in the patient's programmes screen.
The patient accepts or declines. Referral requires a treating relationship (`SEC-10` rule 4).

`PRG-6` **Enrolment** `care_enrollments(id, patient_id, programme_key, offering_id?, source
(self, referral), started_at, ended_at, status)`. Enrolling creates the programme's schedule
(reminders and check-ins as timers) and default alert rules; the patient chooses which guardians
may follow it (consent categories are created per programme: `programme:<key>`).

### Programmes in the MVP

| Programme | Priority | Scope in the MVP |
|---|---|---|
| `elder_care` | P0 | medicines and dose reminders, missed-dose detection, guardian monitoring, check-ins, navigation; the Amma scenario |
| `pregnancy` | P1 | enrolment with due date; antenatal visit reminders from the schedule template; maternal red flags (RF-MAT-001 to 005); partner or family guardian alerts within consent; si and en content |
| `newborn` | P1 | enrolment with birth date; vaccination and check-up reminders from the schedule template; infant red flag (RF-PAED-001); guardian alerts; content |
| `sexual_health` | P0 | the private consult (section 2) plus confidential service navigation |

`PRG-7` P1 programmes are bounded: working enrolment, reminders, red flags, consented alerts and
content. They are labelled as demonstration programmes, not production clinical coverage. Schedule
templates cite the public source they follow (for example the national immunisation schedule) and
are reviewed by a team member before the demo.

## 2. Private consult

Questions about sexual health are more sensitive than any other record. Exposure harms people more
than any other leak. The private consult is designed so that **the server never stores readable
content**, and nothing about it reaches anyone else.

### Boundaries
`PRV-1` A private consult is **not a case**. It has no case events, no timeline, no record
resources, no FHIR export, no cache entries (`KNW-35` never-cached list), no evaluation or training
use, no notifications with content.

`PRV-2` No other person ever sees it: not guardians, doctors, hospital admins or system admins.
There is no admin or support path to its content.

### Processing
`PRV-3` For each turn the browser sends the new message and the minimal recent context needed (at
most the last 10 turns) over TLS. The server processes it **in memory only**: red-flag rules
(rules only), then the model through the gateway with purpose `private_consult`, then the verifier
deterministic checks for wording and safety. The reply is returned. **No message text is written to
the database, logs, traces or files.** `ops_model_calls` records only cost, latency and model, with
no case or patient link.

`PRV-4` **Anonymous to the model:** the gateway sends no identifiers. The patient's record is not
used unless the user switches on "Use my health record" for that session; then only the categories
the user selects are included, through the normal PHI minimization pipeline.

`PRV-5` **Disclosure:** at the start of every session the user sees, in their language: their
messages are processed by an AI service without their name; nothing is saved unless they choose;
how to delete. Starting the session is an explicit action.

`PRV-6` Provider: the configured chat provider must have no data retention for this purpose
(Vertex AI, or OpenRouter restricted to zero-retention providers). If the configured provider cannot
guarantee that, the private consult is disabled and says so. The offline provider works (labelled).

### Optional saved history (client-side encryption)
`PRV-7` By default nothing is saved: the conversation lives only in browser memory and disappears
when the tab closes or the user taps "End and delete".

`PRV-8` If the user chooses "Keep for up to 7 days":
- the browser generates a random 256-bit AES-GCM key (WebCrypto);
- each message is encrypted **in the browser** with a fresh 96-bit nonce; only ciphertext is sent to
  `private_messages(id, session_id, ciphertext, nonce, created_at, expires_at)`;
- the key is stored only on the device (IndexedDB), wrapped with a key derived from the user's PIN
  (PBKDF2-SHA256, 600,000 iterations, random salt). The PIN unlocks the key locally; it never leaves
  the device and is not the only secret: an attacker with the server database has only ciphertext
  and no key material to guess against;
- a one-time **recovery code** (128-bit random, shown once) can re-wrap the key on a new device;
- `expires_at` is at most 7 days. The worker hard-deletes expired rows every hour.

`PRV-9` **Deletion:** "Delete now" deletes server rows and destroys the device key. Because the key
never reaches the server, backups that still contain ciphertext are unreadable (crypto-shredding).
No plaintext temporary files exist on the server by design (`PRV-3`).

`PRV-10` Limitation stated in the UI help text: someone with the unlocked device can read saved
history; a short PIN can be guessed offline by someone who has the device. Users who need more
protection keep the default (nothing saved).

### Safety inside the private consult
`PRV-11` Red-flag rules still run on every message. Self-harm, assault or emergency patterns show
help immediately (1990, the 1926 mental health line, and confidential support services) through
rules only, without waiting for a model.

`PRV-12` The consult can suggest confidential services (for example STD clinics, which are free and
confidential in Sri Lanka) and book them. A booking made from a private consult is tagged
`sensitive_sexual_health`: hidden from guardians, excluded from doctor briefs unless the patient
switches it on for that doctor, and shown to hospital admins as an appointment number only.

### UI
`PRV-13` Neutral name ("Private consult"), its own entry in the patient menu, no previews or
content in notifications, a visible quick-exit button that clears the screen, no browser history
entries for messages, no local storage of plaintext.

### Audit
`PRV-14` Audit records only `private_session.started` and `private_session.ended` with the user ID
and time. No topic, category, programme key or content.
