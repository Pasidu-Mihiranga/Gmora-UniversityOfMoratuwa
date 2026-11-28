"""Red-flag rule set v1 (spec 07 section 3, SAF-3, SAF-4).

A rule fires when any of its patterns matches the concepts found in the patient's words and, when
it has one, its context predicate holds (age, pregnancy, chronic conditions, missed doses). The
urgency floor is the highest urgency of every rule that fires; a model may raise it, never lower
it. Rules are deliberately sensitive: a false alarm costs a doctor visit, a miss can cost a life
(hazard H1). Negation ("no chest pain") is not read: a mention is treated as a report.

The concept groups below are the reviewed rule code. Changing one is a safety change: test first,
in Sinhala and English, and bump `RULES_VERSION`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from suwapath.contracts.enums import Urgency
from suwapath.contracts.ports.safety import PatientContext

RULES_VERSION = "rules-v1"
OLDER_ADULT_AGE = 60  # an elder in Sri Lanka; the same age the UI uses for simple mode (FE-43)
ELDER_CARE_AGE = 65  # RF-GER-003 is for the elder-care programme
CARDIAC_RISK_AGE = 50
CARDIAC_RISK_CODES = (
    "diabetes",
    "hypertension",
    "ischaemic_heart_disease",
    "dyslipidaemia",
    "ckd",
)

Concepts = frozenset[str]
Predicate = Callable[[PatientContext, Concepts], bool]


@dataclass(frozen=True)
class Pattern:
    """All of `all_of`, at least one of `any_of` (if given) and at least `min_count` of `some_of`
    (if given)."""

    all_of: Concepts = frozenset()
    any_of: Concepts = frozenset()
    some_of: Concepts = frozenset()
    min_count: int = 0

    def matches(self, found: Concepts) -> bool:
        if not self.all_of <= found:
            return False
        if self.any_of and not self.any_of & found:
            return False
        return not (self.some_of and len(self.some_of & found) < self.min_count)

    def concepts(self) -> Concepts:
        return self.all_of | self.any_of | self.some_of


@dataclass(frozen=True)
class Rule:
    rule_id: str
    label: str
    urgency: Urgency
    patterns: tuple[Pattern, ...]
    specialty: str
    rationale: str
    when: Predicate | None = None
    help_lines: tuple[str, ...] = ("1990",)

    def fires(self, found: Concepts, context: PatientContext) -> tuple[str, ...] | None:
        """The concepts that made it fire, or None."""
        matched = [p for p in self.patterns if p.matches(found)]
        if not matched or (self.when is not None and not self.when(context, found)):
            return None
        return tuple(sorted(set().union(*(p.concepts() & found for p in matched))))

    def concepts(self) -> Concepts:
        return frozenset().union(*(p.concepts() for p in self.patterns))


def _c(*names: str) -> Concepts:
    return frozenset(names)


# ----- context predicates -----------------------------------------------------------------------


def older_adult(context: PatientContext, found: Concepts) -> bool:
    return context.age_years is not None and context.age_years >= OLDER_ADULT_AGE


def elder_with_missed_medicine(context: PatientContext, found: Concepts) -> bool:
    elder = context.age_years is not None and context.age_years >= ELDER_CARE_AGE
    return elder and (context.missed_doses_48h > 0 or "missed_medicine" in found)


def _has(context: PatientContext, *prefixes: str) -> bool:
    return any(code.startswith(prefixes) for code in context.chronic_codes)


def cardiac_risk(context: PatientContext, found: Concepts) -> bool:
    old = context.age_years is not None and context.age_years >= CARDIAC_RISK_AGE
    return old or _has(context, *CARDIAC_RISK_CODES)


def diabetic(context: PatientContext, found: Concepts) -> bool:
    return _has(context, "diabetes")


def pregnant(context: PatientContext, found: Concepts) -> bool:
    return context.pregnant or "pregnant" in found


def postpartum(context: PatientContext, found: Concepts) -> bool:
    return context.postpartum or "postpartum" in found


def infant(context: PatientContext, found: Concepts) -> bool:
    return context.age_years == 0 or "baby" in found


# ----- rule set v1 ------------------------------------------------------------------------------

E, U = Urgency.EMERGENCY, Urgency.URGENT_24H

RULES: tuple[Rule, ...] = (
    Rule(
        "RF-CARD-001",
        "Possible acute coronary syndrome",
        E,
        (
            Pattern(
                all_of=_c("chest_pain"),
                any_of=_c("pain_spreading", "sweating", "breathless", "vomiting"),
            ),
        ),
        "cardiology",
        "Chest pain with spreading pain, sweating, breathlessness or vomiting.",
    ),
    Rule(
        "RF-CARD-002",
        "Chest pain with cardiac risk factors",
        E,
        (Pattern(all_of=_c("chest_pain")),),
        "cardiology",
        "Chest pain in a person aged 50 or over, or with diabetes, high blood pressure, heart "
        "disease, high cholesterol or kidney disease.",
        when=cardiac_risk,
    ),
    Rule(
        "RF-CARD-003",
        "Collapse or loss of consciousness",
        E,
        (Pattern(any_of=_c("collapse")),),
        "emergency_medicine",
        "Fainting, collapse or not responding.",
    ),
    Rule(
        "RF-NEURO-001",
        "Possible stroke (FAST pattern)",
        E,
        (Pattern(any_of=_c("face_droop", "one_side_weak", "slurred_speech")),),
        "neurology",
        "Any one FAST sign: face drooping, arm or one-sided weakness, speech trouble.",
    ),
    Rule(
        "RF-NEURO-002",
        "Possible meningitis",
        E,
        (
            Pattern(all_of=_c("fever"), any_of=_c("stiff_neck", "rash_not_fading")),
            Pattern(all_of=_c("high_fever"), any_of=_c("stiff_neck", "rash_not_fading")),
            Pattern(all_of=_c("headache", "stiff_neck")),
        ),
        "neurology",
        "Fever with a stiff neck or a rash that does not fade, or headache with a stiff neck.",
    ),
    Rule(
        "RF-NEURO-003",
        "Thunderclap or worst-ever headache",
        E,
        (Pattern(any_of=_c("worst_headache")),),
        "neurology",
        "A sudden, severe or worst-ever headache.",
    ),
    Rule(
        "RF-NEURO-004",
        "Active seizure",
        E,
        (Pattern(any_of=_c("seizure")),),
        "emergency_medicine",
        "A fit or convulsion.",
    ),
    Rule(
        "RF-RESP-001",
        "Severe breathing difficulty",
        E,
        (Pattern(any_of=_c("severe_breathless", "blue_lips")),),
        "emergency_medicine",
        "Cannot breathe, gasping, or blue lips.",
    ),
    Rule(
        "RF-RESP-002",
        "Coughing blood",
        U,
        (Pattern(any_of=_c("coughing_blood")),),
        "respiratory_medicine",
        "Blood when coughing.",
    ),
    Rule(
        "RF-RESP-003",
        "Fever with breathing difficulty",
        U,
        (Pattern(any_of=_c("fever", "high_fever"), all_of=_c("breathless")),),
        "respiratory_medicine",
        "Fever together with shortness of breath.",
    ),
    Rule(
        "RF-BLEED-001",
        "Severe or uncontrolled bleeding",
        E,
        (Pattern(any_of=_c("heavy_bleeding")),),
        "emergency_medicine",
        "Heavy bleeding or bleeding that does not stop.",
    ),
    Rule(
        "RF-GI-001",
        "Gastrointestinal bleeding",
        E,
        (Pattern(any_of=_c("vomiting_blood", "gut_bleeding")),),
        "gastroenterology",
        "Vomiting blood, black stools or blood in the stool.",
    ),
    Rule(
        "RF-GI-002",
        "Severe abdominal pain",
        U,
        (Pattern(any_of=_c("severe_abdominal_pain")),),
        "general_surgery",
        "Severe or unbearable stomach pain.",
    ),
    Rule(
        "RF-MAT-001",
        "Bleeding in pregnancy",
        E,
        (Pattern(any_of=_c("bleeding", "heavy_bleeding")),),
        "obstetrics",
        "Any bleeding during pregnancy.",
        when=pregnant,
    ),
    Rule(
        "RF-MAT-002",
        "Pre-eclampsia warning signs in pregnancy",
        E,
        (Pattern(any_of=_c("headache", "worst_headache", "blurred_vision", "sudden_swelling")),),
        "obstetrics",
        "Headache, blurred vision or sudden swelling of the face or hands during pregnancy.",
        when=pregnant,
    ),
    Rule(
        "RF-MAT-003",
        "Baby moving less in pregnancy",
        U,
        (Pattern(any_of=_c("baby_not_moving")),),
        "obstetrics",
        "The baby is moving less or has stopped moving.",
        when=pregnant,
    ),
    Rule(
        "RF-MAT-004",
        "Waters broken or fluid leaking in pregnancy",
        U,
        (Pattern(any_of=_c("waters_broke")),),
        "obstetrics",
        "Fluid leaking from the vagina during pregnancy.",
        when=pregnant,
    ),
    Rule(
        "RF-MAT-005",
        "Fever or bleeding after giving birth",
        E,
        (Pattern(any_of=_c("fever", "high_fever", "bleeding", "heavy_bleeding")),),
        "obstetrics",
        "Fever or bleeding in the weeks after giving birth.",
        when=postpartum,
    ),
    Rule(
        "RF-MH-001",
        "Risk of self-harm",
        E,
        (Pattern(any_of=_c("self_harm")),),
        "psychiatry",
        "Thoughts of suicide or self-harm.",
        help_lines=("1990", "1926"),
    ),
    Rule(
        "RF-PAED-001",
        "Unwell infant",
        E,
        (
            Pattern(
                any_of=_c(
                    "fever",
                    "high_fever",
                    "not_feeding",
                    "very_drowsy",
                    "breathless",
                    "severe_breathless",
                    "blue_lips",
                    "seizure",
                )
            ),
        ),
        "paediatrics",
        "A baby with fever, not feeding, very drowsy, breathing trouble or blue lips.",
        when=infant,
    ),
    Rule(
        "RF-GER-001",
        "Fall with injury in an older adult",
        U,
        (Pattern(all_of=_c("fall"), any_of=_c("injury")),),
        "orthopaedics",
        "A fall with pain, swelling, a head knock or not able to get up, at 60 or over.",
        when=older_adult,
    ),
    Rule(
        "RF-GER-002",
        "New confusion in an older adult",
        E,
        (Pattern(any_of=_c("confusion")),),
        "emergency_medicine",
        "New confusion at 60 or over.",
        when=older_adult,
    ),
    Rule(
        "RF-GER-003",
        "Dizziness in an older adult with missed medicines",
        U,
        (Pattern(all_of=_c("dizzy")),),
        "general_medicine",
        "Dizziness at 65 or over with medicines missed in the last 48 hours.",
        when=elder_with_missed_medicine,
    ),
    Rule(
        "RF-MET-001",
        "Possible low blood sugar in a person with diabetes",
        E,
        (
            Pattern(any_of=_c("low_sugar")),
            Pattern(some_of=_c("sweating", "shaking", "confusion", "dizzy"), min_count=2),
        ),
        "endocrinology",
        "Low sugar, or two of sweating, shaking, confusion and dizziness, with diabetes.",
        when=diabetic,
    ),
    Rule(
        "RF-INF-001",
        "Persistent high fever",
        U,
        (
            Pattern(all_of=_c("fever", "several_days")),
            Pattern(all_of=_c("high_fever", "several_days")),
        ),
        "general_medicine",
        "Fever lasting three days or more.",
    ),
)
