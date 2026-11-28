# 08. Care programmes, navigation and booking

## 1. Care (module `care`)

### Programmes

`CARE-1` A care programme implements `CareProgramme` (`15-extensions-and-mcp.md`):
`key`, `name` (si, en), `eligibility(patient) -> bool`, `default_alert_rules()`,
`default_checkin_schedule()`, `dashboard_cards()`. MVP registers `elder_care`.
Pregnancy, newborn and sexual-health programmes are later registrations, not code changes.

### Medication schedule and doses

`CARE-2` A scheduled dose is derived from each active `MedicationStatement.dosage.timing`
(times of day and days). Default slot times: morning 08:00, noon 13:00, evening 19:00,
night 22:00 (patient can change in settings).

`CARE-3` For each dose, `care` creates a `dose_check` timer (`EVT-14`) for the next 48 h on a
rolling basis (a daily timer extends the window). Timers are idempotent by
`dedupe_key = "dose:{statement_id}:{scheduled_at}"`.

`CARE-4` The patient marks a dose taken (tap, or by telling the companion). This writes
`MedicationAdministration(status=completed)` and `care.dose_taken`, and cancels the timer.

`CARE-5` When the timer fires with no administration recorded, `care` writes
`MedicationAdministration(status=not-done, reason=missed)` and emits `care.dose_missed`.

`CARE-6` `adherence_summary(patient, days=7)` returns per day and slot: taken, missed, pending;
plus streaks and the missed count per slot. Only categories `adherence` and `medications`
(names, not doses) are included.

### Alert rules

`CARE-7` `AlertRule` registry. MVP rule types:

| Type | Parameters | Fires when |
|---|---|---|
| `missed_doses` | count, window (hours), slot (optional) | `count` doses missed in `window` (optionally only that slot) |
| `no_checkin_response` | hours | patient did not answer a check-in |
| `red_flag` | none, always on | any red flag on a case of the patient |

`CARE-8` A guardian rule ("Alert me if 2 evening doses are missed") is stored per
(guardian, patient), versioned, and may only reference categories the guardian is granted. A fired
rule triggers the guardian agent (it does not message the guardian directly).

## 2. Navigation (module `navigation`)

### Navigation engine (knowledge-driven, not an LLM decision)

Navigation answers two questions: **how soon** (urgency window) and **which kind of doctor**
(specialty). It never names a condition to the patient. It is a deterministic engine over a
structured knowledge graph, exposed to the companion agent as tools. There is no navigation agent.

`NAV-1` **Safeguard 1, red flags first.** Every patient message is assessed by the red-flag rules
(`07-clinical-safety.md`) **before** navigation, booking, cache lookup or any ordinary question.
Any answer at any point is re-assessed. The question limit never delays an emergency: red-flag
screening questions are asked first and do not count toward the limit.

`NAV-2` **Safeguard 2, confidence is not a diagnosis.** The engine keeps internal condition-group
hypotheses only to choose questions and rank specialties. Hypotheses are never shown to patients,
guardians or doctors, never stored as conditions in the record, and never sent in messages. Low
confidence means a general physician first, never a forced specialist.

`NAV-3` **Safeguard 3, booking never overrides safety.** Direct channelling requests may skip the
chat model (`KNW-1` T2), but any symptom words in them still go through the red-flag rules, and
ranking never uses commissions, booking fees, listing fees or any commercial input.

#### Specialty catalog

`NAV-3a` Specialties (enum `Specialty` in contracts): general_medicine, cardiology, endocrinology,
nephrology, neurology, respiratory, gastroenterology, orthopaedics, rheumatology, dermatology, ent,
ophthalmology, psychiatry, obstetrics_gynaecology, paediatrics, general_surgery, urology,
venereology (confidential sexual health clinics, used by the private consult `PRV-12`). The engine
and any model step can only return values from this catalog.

#### Knowledge graph

`NAV-4` Static, versioned files in `modules/navigation/kb/` (loaded at startup, `kb_version` recorded
with every navigation result):

| File | Content | Source |
|---|---|---|
| `concepts.yaml` | finding concepts (symptoms, risk factors, context) with si and en lexicon terms and ambiguity notes | team lexicon |
| `condition_groups.yaml` | condition groups with evidence likelihoods P(finding given group), prior by age band and sex | DDXPlus knowledge base (CC BY 4.0) mapped to our concepts, plus an elder-care and chronic-disease extension written by the team with references |
| `routing.yaml` | condition group to specialty (ranked) and urgency window | team labels (task D01), reviewed |
| `questions.yaml` | question per concept in si and en, answer type (`yes_no`, `choice`, `number`, `duration`), which concepts the answer sets | team |

`NAV-5` DDXPlus covers 49 conditions, mostly respiratory, infectious and acute presentations. The
extension adds at least: diabetes control problems, hypertension control problems, medication side
effects, osteoarthritis flare, chronic kidney disease concerns, urinary symptoms, falls, dizziness
(vertigo vs light-headedness), low mood, memory concerns. Each extension group lists its references.

#### Engine steps

1. **Extract findings.** The lexicon finds concepts in the text. If a chat model is configured, it
   also extracts findings into a typed schema (`concept`, `present` or `absent`, `onset`,
   `duration`, `severity`, `site`) limited to known concept IDs. Extraction can add findings, never
   remove lexicon findings.
2. **Ambiguity check.** Concepts marked ambiguous (for example "dizzy": spinning or faint) produce a
   clarifying question before scoring.
3. **Red-flag screen** (`NAV-1`).
4. **Score hypotheses.** Naive Bayes over condition groups: prior (age band, sex, known chronic
   conditions from the consented record) times likelihoods of present and absent findings.
   Distribution over groups is converted to a distribution over specialties and urgency windows
   through `routing.yaml`.
5. **Choose the next question** with the highest expected information gain over the
   **specialty and urgency** distribution (not over condition groups), among unasked concepts.
6. **Stop** when the top specialty probability is at least 0.75 and the urgency window is settled
   (top urgency probability at least 0.8), or after 6 non-red-flag questions, or when the patient
   asks to stop.
7. **Decide.** Urgency window = max(red-flag floor, most probable window, any window with
   probability at least 0.2 that is more urgent). This errs toward sooner care.
   Specialty: if top probability is at least 0.75, that specialty; otherwise **general medicine
   first** plus up to 3 alternative specialties.

`NAV-6` Output:

```python
class NavigationResult(BaseModel):
    urgency: Urgency                       # emergency, urgent_24h, soon_72h, routine
    urgency_floor: Urgency | None          # from red-flag rules
    primary_specialty: Specialty
    alternatives: list[SpecialtyOption]    # up to 3, each with a plain-language reason key
    confidence: float                      # top specialty probability
    low_confidence: bool                   # true -> primary is general_medicine
    findings: list[Finding]                # present findings with provenance (patient_reported)
    negatives: list[Finding]               # findings the patient said were absent
    questions_asked: int
    kb_version: str
    rules_version: str
```

`NAV-7` The patient sees: the urgency window in plain words, the recommended doctor type with one
plain reason, and a carousel of doctors (primary first, then alternatives), each card saying why
("Most people with these symptoms start with a general physician"). Never a condition name.

`NAV-8` The doctor brief (`05-agents-and-verifier.md`) includes a **navigation summary**: findings,
negatives, urgency and the specialties considered. It never includes hypotheses.

`NAV-9` The 85% right-specialty-first-time figure is an **evaluation KPI**, measured on an
independently reviewed test set (two labellers plus a third for disagreements, `13-evaluation.md`).
It is never presented to patients as a guarantee.

#### Tools exposed to the companion

| Tool | Purpose |
|---|---|
| `navigation.start(case_id, text)` | extract findings, run the screen, return the first question or a result |
| `navigation.answer(case_id, concept, answer)` | update findings, return next question or result |
| `navigation.result(case_id)` | current `NavigationResult` |
| `navigation.find_doctors(...)` | `NAV-10` |
| `navigation.plan_trip(...)` | `NAV-12` |

Navigation state per case is stored as case events (`FactRecorded`, `UrgencyAssessed`,
`RouteSuggested`), so it is replayable and audited.

#### Booking follow-up questions

`NAV-14` Before searching, the companion fills the booking slots it needs and asks only for the ones
that are missing and matter, **at most 2 questions**, offered as tap choices rather than free text:

| Slot | Default when not asked | Ask when |
|---|---|---|
| date window | from the urgency window (`today`, within 3 days, within 2 weeks) | urgency is `soon_72h` or `routine` |
| time of day | any | always offered as a choice (morning, afternoon, evening, any) |
| area | patient's home district | the patient may be elsewhere (asked once, remembered for the case) |
| doctor language | patient's language preferred | never asked, used for ranking |
| specific doctor or hospital | none | the patient mentioned one, or has a treating doctor in that specialty |

`NAV-15` **Continuity of care.** If the patient already has a treating doctor in the recommended
specialty, that doctor is offered first ("Continue with Dr. Silva?") when they have a slot within
the urgency window. Ranking order: clinical fit, continuity, earliest slot in the window, distance,
fee.

`NAV-16` **Several doctors in one trip only when needed.** The trip planner (`NAV-12`) is offered
only when the patient has two or more open needs (for example two referrals, or follow-ups due with
two specialists) or asks for it. It is never forced on a single need.

#### Direct channelling

`NAV-13` "I need to channel Dr. Silva today" is classified as `book_or_find_doctor` (`KNW-3`) with
slots: doctor name (trigram fuzzy match), specialty, hospital, date window (`today`, `tomorrow`,
`this_week`, a date), time of day, language. Any symptom words run through the red-flag rules first.
Same-day means sessions today whose estimated slot time is after now plus estimated travel time,
with free slots. The companion shows matching doctors and holds the chosen slot for the patient's tap.

### Doctor matching

`NAV-10` `find_doctors(specialty, near: (lat, lng), date_range, language, max_km=25, limit=10)`.
Filters: specialty, has free slots in range, speaks the patient's language (preferred, not
required). Ranking: clinical fit (primary before alternatives), then earliest available slot within
the urgency window, then distance (haversine), then fee. **Commissions, booking fees and listing
fees never affect ranking** (proposal 2.3, `NAV-3`).

`NAV-11` Distance uses a bounding-box prefilter on indexed `lat` and `lng`, then exact haversine.
The directory comes through ports, never another module's tables: `booking.doctor_candidates
(specialty, box, date_from, date_to, organization_id)` returns the doctors of a specialty at
hospitals in the box with their free slots in the window (no ranking), and
`record.practitioner_cards(ids)` and `record.search_practitioners(name)` give the card name, the
languages and the trigram name match. Navigation filters and ranks.

### Several doctors in one trip

`NAV-12` `plan_trip(specialties, near, date_range, max_options=3)` finds days where the patient can
see every requested specialty, at one facility or at facilities less than 5 km apart, with at least
20 minutes between appointments (plus travel time at 20 km/h between facilities). It returns up to
3 itineraries ranked by total waiting plus travel time. Search is bounded: at most 5 candidate
doctors per specialty and 14 days.

## 3. Booking (module `booking`)

### Directory and sessions

`BOOK-1` `Organization` (hospital), `Practitioner`, `PractitionerRole` (doctor at hospital with
specialty and fee), `Schedule` (a channelling session: doctor, hospital, date, start, end, number of
patients), `Slot` (one appointment number in a session with an estimated time).

### Holds and confirmation

`BOOK-2` Flow: `hold_slot(slot, case)` -> hold for 5 minutes -> the patient taps confirm ->
`confirm(hold, idempotency_key)` -> `Appointment(status=booked)` -> `booking.appointment_booked`.
Agents can create holds and proposals; only a person's action confirms (`ToolRisk.write_irreversible`).

`BOOK-3` **No double booking:** a partial unique index on `booking_appointments(slot_id) WHERE
status IN ('held', 'booked')`. A conflicting insert returns "slot just taken" and the next best slot.

`BOOK-4` `confirm` with the same `Idempotency-Key` returns the same appointment and never creates
a second one.

`BOOK-5` Booking is refused with `SafetyStop` when the case is in `emergency`.

`BOOK-6` Cancellation by the patient or the hospital emits `booking.appointment_cancelled`. For a
hospital cancellation, the companion prepares an alternative for the patient's tap.

### Hospital view

`BOOK-7` Hospital admins see their sessions, booked counts, and the appointment list with patient
first name and appointment number only. No clinical data.
