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

### Specialty routing

`NAV-1` `route(concepts, context, language) -> RouteSuggestion(specialties: list[(specialty,
score)], urgency, explanation_key, confidence)`.

`NAV-2` Two steps: deterministic concept-to-specialty weights (from the lexicon) produce a ranked
list and a confidence from the score margin. If confidence < 0.6 and a model is available, the model
chooses among the **top 5 catalog specialties only** (structured output with an enum). The model
can never return a specialty outside the catalog.

`NAV-3` Specialty catalog (MVP): general_medicine, cardiology, endocrinology, nephrology,
neurology, respiratory, gastroenterology, orthopaedics, rheumatology, dermatology, ent,
ophthalmology, psychiatry, obstetrics_gynaecology, paediatrics, general_surgery, urology.

### Doctor matching

`NAV-4` `find_doctors(specialty, near: (lat, lng), date_range, language, max_km=25, limit=10)`.
Filters: specialty, has free slots in range, speaks the patient's language (preferred, not
required). Ranking: earliest available slot, then distance (haversine), then fee. **Payment or
listing fees never affect ranking** (proposal 2.3).

`NAV-5` Distance uses a bounding-box prefilter on indexed `lat` and `lng`, then exact haversine.

### Several doctors in one trip

`NAV-6` `plan_trip(specialties, near, date_range, max_options=3)` finds days where the patient can
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
