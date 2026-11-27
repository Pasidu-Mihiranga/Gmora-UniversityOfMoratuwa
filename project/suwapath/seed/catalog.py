"""Reference data for the synthetic world. Invented names; real district coordinates."""

from __future__ import annotations

from typing import Literal

from suwapath.contracts.enums import Specialty

HOSPITALS: list[tuple[str, str, float, float]] = [
    ("Lotus Care Hospital Kurunegala", "Kurunegala", 7.4863, 80.3647),
    ("Sunrise General Hospital Colombo", "Colombo", 6.9271, 79.8612),
    ("Hill Country Medical Centre Kandy", "Kandy", 7.2906, 80.6337),
    ("Southern Coast Hospital Galle", "Galle", 6.0535, 80.2210),
    ("Lagoon Medical Centre Negombo", "Gampaha", 7.2083, 79.8358),
    ("Green Valley Hospital Maharagama", "Colombo", 6.8480, 79.9265),
]

SPECIALTY_MIX: list[Specialty] = [
    Specialty.GENERAL_MEDICINE,
    Specialty.GENERAL_MEDICINE,
    Specialty.GENERAL_MEDICINE,
    Specialty.CARDIOLOGY,
    Specialty.ENDOCRINOLOGY,
    Specialty.NEPHROLOGY,
    Specialty.NEUROLOGY,
    Specialty.RESPIRATORY,
    Specialty.GASTROENTEROLOGY,
    Specialty.ORTHOPAEDICS,
    Specialty.RHEUMATOLOGY,
    Specialty.DERMATOLOGY,
    Specialty.ENT,
    Specialty.OPHTHALMOLOGY,
    Specialty.PSYCHIATRY,
    Specialty.OBSTETRICS_GYNAECOLOGY,
    Specialty.PAEDIATRICS,
    Specialty.GENERAL_SURGERY,
    Specialty.UROLOGY,
    Specialty.VENEREOLOGY,
    Specialty.CARDIOLOGY,
    Specialty.ENDOCRINOLOGY,
    Specialty.GENERAL_MEDICINE,
    Specialty.PAEDIATRICS,
]

GIVEN_FEMALE = [
    "Kamala",
    "Sumana",
    "Nirmala",
    "Chandrika",
    "Malini",
    "Anoma",
    "Shanthi",
    "Fathima",
    "Kalaivani",
    "Dilani",
    "Nadeesha",
    "Ishara",
    "Tharushi",
    "Rukshana",
    "Priyanka",
]
GIVEN_MALE = [
    "Nimal",
    "Sunil",
    "Ranjith",
    "Gamini",
    "Lalith",
    "Mohamed",
    "Suresh",
    "Kumar",
    "Ruwan",
    "Chaminda",
    "Kasun",
    "Dinesh",
    "Tharindu",
    "Ashan",
    "Nuwan",
]
FAMILY = [
    "Perera",
    "Fernando",
    "Silva",
    "Jayasinghe",
    "Wickramasinghe",
    "Bandara",
    "Herath",
    "Dissanayake",
    "Rajapaksha",
    "Kumarasinghe",
    "Mohideen",
    "Nadarajah",
    "Ratnayake",
    "Gunasekara",
    "Weerasinghe",
]

# (code, display, typical in older adults)
CONDITIONS: list[tuple[str, str]] = [
    ("diabetes_type2", "Type 2 diabetes"),
    ("hypertension", "High blood pressure"),
    ("osteoarthritis", "Osteoarthritis"),
    ("ckd_stage3", "Chronic kidney disease stage 3"),
    ("copd", "COPD"),
    ("ischaemic_heart_disease", "Ischaemic heart disease"),
    ("hypothyroidism", "Hypothyroidism"),
    ("dyslipidaemia", "High cholesterol"),
]

# medicine -> (drug class, conditions it treats, default slots)
FORMULARY: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "metformin": ("biguanide", ("diabetes_type2",), ("morning", "evening")),
    "glibenclamide": ("sulfonylurea", ("diabetes_type2",), ("morning",)),
    "amlodipine": ("calcium_channel_blocker", ("hypertension",), ("evening",)),
    "losartan": ("arb", ("hypertension", "ckd_stage3"), ("morning",)),
    "atorvastatin": ("statin", ("dyslipidaemia", "ischaemic_heart_disease"), ("night",)),
    "rosuvastatin": ("statin", ("dyslipidaemia",), ("night",)),
    "aspirin": ("antiplatelet", ("ischaemic_heart_disease",), ("noon",)),
    "clopidogrel": ("antiplatelet", ("ischaemic_heart_disease",), ("morning",)),
    "warfarin": ("anticoagulant", ("ischaemic_heart_disease",), ("evening",)),
    "levothyroxine": ("thyroid_hormone", ("hypothyroidism",), ("morning",)),
    "salbutamol_inhaler": ("beta_agonist", ("copd",), ()),
    "paracetamol": ("analgesic", ("osteoarthritis",), ()),
    "diclofenac": ("nsaid", ("osteoarthritis",), ("noon",)),
    "omeprazole": ("ppi", (), ("morning",)),
    "amoxicillin": ("penicillin", (), ("morning", "noon", "evening")),
}

ALLERGIES: list[tuple[str, str]] = [
    ("penicillin", "penicillin"),
    ("sulfonamide", "sulfa_drug"),
    ("nsaid", "nsaid"),
    ("iodine_contrast", "contrast"),
]

# LOINC where well known; reference ranges as printed on the synthetic reports.
LAB_TESTS: list[tuple[str, str, str, float, float]] = [
    ("4548-4", "HbA1c", "%", 4.0, 5.6),
    ("1558-6", "Fasting blood sugar", "mg/dL", 70.0, 100.0),
    ("2160-0", "Serum creatinine", "mg/dL", 0.6, 1.2),
    ("2093-3", "Total cholesterol", "mg/dL", 0.0, 200.0),
    ("13457-7", "LDL cholesterol", "mg/dL", 0.0, 130.0),
]

ADHERENCE_PATTERNS: tuple[Literal["good", "slipping", "evening_misses"], ...] = (
    "good",
    "slipping",
    "evening_misses",
)
