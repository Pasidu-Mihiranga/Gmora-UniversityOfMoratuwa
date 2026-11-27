# 07. Clinical safety

## 1. Scope

`SAF-1` SuwaPath helps people decide where to go and how soon, and helps them get there. It MUST
NOT diagnose, prescribe, change doses, interpret a result as a diagnosis, or reassure.
Every patient-facing answer ends in navigation: a specialty, a doctor, a booking, or a safety step.

## 2. Urgency levels

| Urgency | Patient sees (en) | Action | Booking |
|---|---|---|---|
| `emergency` | "This may be an emergency. Call 1990 now." | 1990 card, nearest emergency unit, guardian emergency alert | stopped |
| `urgent_24h` | "Please see a doctor today." | earliest same-day slot, guardian informed per consent | allowed, today only |
| `soon_72h` | "Please see a doctor within 3 days." | slots within 72 h | allowed |
| `routine` | "Please see a doctor within 2 weeks." | slots within 14 days | allowed |

Sinhala wording for every message lives in `modules/safety/content/si.yaml` and is reviewed by a
native speaker on the team before the demo.

## 3. Red-flag rules and the urgency floor

`SAF-2` Concepts are extracted from patient text by the lexicon (Sinhala, Sinhala written in Latin
script, English). The model MAY add concepts it detects; it MUST NOT remove lexicon concepts.

`SAF-3` `urgency_floor = max(urgency of every rule that fires)`. The final urgency is
`max(urgency_floor, model_suggestion)`. The model can raise urgency, never lower it.

`SAF-4` Rules are data plus small predicates, each with an ID, label, concept groups
(`all_of`, `any_of`, `any_of_min`), optional context predicate (age, pregnancy, chronic conditions),
urgency, rationale, and the specialty to route to. The rule set is versioned (`rules_version`)
and recorded with every assessment.

Rule set v1 (reviewed, including two elder-care rules):

| ID | Label | Urgency |
|---|---|---|
| RF-CARD-001 | Possible acute coronary syndrome | emergency |
| RF-CARD-002 | Chest pain with cardiac risk factors | emergency |
| RF-CARD-003 | Collapse or loss of consciousness | emergency |
| RF-NEURO-001 | Possible stroke (FAST pattern) | emergency |
| RF-NEURO-002 | Possible meningitis | emergency |
| RF-NEURO-003 | Thunderclap or worst-ever headache | emergency |
| RF-NEURO-004 | Active seizure | emergency |
| RF-RESP-001 | Severe breathing difficulty | emergency |
| RF-RESP-002 | Coughing blood | urgent_24h |
| RF-RESP-003 | Fever with breathing difficulty | urgent_24h |
| RF-BLEED-001 | Severe or uncontrolled bleeding | emergency |
| RF-GI-001 | Gastrointestinal bleeding | emergency |
| RF-GI-002 | Severe abdominal pain | urgent_24h |
| RF-MAT-001 to 005 | Pregnancy and postpartum warning signs | emergency or urgent_24h |
| RF-MH-001 | Risk of self-harm | emergency (also shows the 1926 mental health line) |
| RF-PAED-001 | Unwell infant | emergency |
| RF-GER-001 | Fall with injury in an older adult | urgent_24h |
| RF-GER-002 | New confusion in an older adult | emergency |
| RF-GER-003 (new) | Dizziness in an older adult with missed medicines | urgent_24h |
| RF-MET-001 (new) | Possible low blood sugar in a person with diabetes (sweating, shaking, confusion) | emergency |
| RF-INF-001 | Persistent high fever | urgent_24h |

The exact concept groups are part of the rule code and its tests. Each rule has at least one firing
test and one near-miss test, in Sinhala and English.

`SAF-5` An imaging flag (`imaging.screening_completed` with `flagged=true`) raises the case floor to
at least `urgent_24h` (`IMG-7`).

## 4. Emergency fast lane

`SAF-10` When the floor is `emergency`:
1. the case moves to `emergency` (`RedFlagRaised`) in the same transaction as the assessment;
2. all slot holds for the case are released and no new booking can be made for it (`SafetyStop`);
3. the patient sees the 1990 card with a call button and the nearest emergency units;
4. the guardian receives the emergency alert (`SEC-10` rule 7) through the verifier's fast path
   (stage 1 checks only, because delay costs more than model review here, and the content is the
   fixed `EMERGENCY_ALERT_FIELDS` template);
5. the companion asks no more intake questions except "Are you able to call 1990 now?".

`SAF-11` Emergency handling never waits for a model call. If the model is slow or down, the
emergency lane still completes.

## 5. Wording rules

`SAF-20` Banned in patient-facing text (both languages, checked by the verifier `wording` check and
by the content review of static strings):
- diagnosis statements ("you have pneumonia", "this is diabetes", "ඔබට ... රෝගය ඇත");
- reassurance ("you are fine", "nothing to worry about", "no need to see a doctor");
- dosing instructions ("take two tablets", "stop your medicine", "increase the dose");
- probabilities from imaging or models;
- the words "diagnosis", "triage", "our doctor", "medical advice" in patient UI copy.

`SAF-21` The pattern lists are data files (`modules/safety/content/banned.si.yaml`,
`banned.en.yaml`) with tests that show each pattern catching an example and not catching a safe
near-miss.

## 6. Clash list

`SAF-30` `safety.check_clashes(medications, allergies, conditions)` returns clashes with severity
(`contraindicated`, `major`, `moderate`) and a source reference. It is deterministic and uses a
small **demo formulary** of about 40 medicines common in older Sri Lankan adults (for example
metformin, glibenclamide, insulin, amlodipine, losartan, enalapril, atorvastatin, aspirin,
clopidogrel, warfarin, diclofenac, ibuprofen, omeprazole, salbutamol, prednisolone, amoxicillin,
co-amoxiclav, cephalexin, ciprofloxacin, paracetamol).

`SAF-31` The formulary file states that it is for demonstration with synthetic patients and not for
clinical use, and lists the public references used for each interaction.

`SAF-32` Allergy checks match by substance and drug class (for example a penicillin allergy matches
amoxicillin and co-amoxiclav).

## 7. Hazard log (MVP)

| Hazard | Cause | Control | Verified by |
|---|---|---|---|
| H1 Emergency under-triaged | lexicon misses phrasing, model lowers urgency | floor in code, model can only raise, red-flag suite per language | red-flag suite recall >= 95% |
| H2 Diagnosis shown to patient | model wording | verifier wording and `implies_diagnosis` checks, doctor approval | injection and wording tests |
| H3 Missed allergy or clash in brief | model omission | clash list deterministic, brief section always present | brief suite: zero missed |
| H4 Guardian learns more than allowed | over-asking, injection | access policy, verifier scope and canary checks | privacy suite: leakage <= 1% |
| H5 Alert fatigue or missed decline | thresholds | check-in first, guardian rules, monitoring suite | false alerts and delay metrics |
| H6 X-ray result read as diagnosis | UI wording | patient sees only "sent for review"; doctor sees validation status | UI review, wording tests |
| H7 Booking during emergency | race | `SafetyStop` in booking when case is `emergency`, holds released in same transaction | integration test |
