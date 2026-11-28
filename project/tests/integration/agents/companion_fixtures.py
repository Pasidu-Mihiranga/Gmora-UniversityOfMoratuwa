"""Offline fixtures for the companion: Amma and variants (A03). Synthetic data only.

The navigation engine (M07) is scripted here, step by step, so the companion's behaviour can be
checked on its own: what it asks, what it records, what it proposes and what it refuses.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from suwapath.contracts.enums import Language, Specialty, Urgency
from suwapath.contracts.ids import OrganizationId, PractitionerId, PractitionerRoleId, SlotId
from suwapath.contracts.ports.navigation import (
    DoctorOption,
    NavigationQuestion,
    NavigationResult,
    NavigationStep,
)

AMMA_FIRST_NAME = "Kamala"  # synthetic
AMMA_AGE = 72


def question(concept: str, answer_type: str = "yes_no", **extra: object) -> NavigationStep:
    return NavigationStep(
        question=NavigationQuestion(
            concept=concept,
            question_key=f"q.{concept}",
            answer_type=answer_type,  # type: ignore[arg-type]
            **extra,  # type: ignore[arg-type]
        )
    )


def result(urgency: Urgency, specialty: Specialty = Specialty.GENERAL_MEDICINE) -> NavigationStep:
    return NavigationStep(
        result=NavigationResult(
            urgency=urgency,
            urgency_floor=None,
            primary_specialty=specialty,
            confidence=0.8,
            low_confidence=False,
            kb_version="kb-test-1",
            rules_version="rules-test-1",
        )
    )


EMERGENCY_STOP = NavigationStep(emergency=True)


def doctor(slot_id: SlotId, at: datetime, role_id: PractitionerRoleId) -> DoctorOption:
    return DoctorOption(
        practitioner_id=PractitionerId(uuid4()),
        practitioner_role_id=role_id,
        display_name="Dr. Perera",  # synthetic directory entry
        specialty=Specialty.GENERAL_MEDICINE,
        organization_id=OrganizationId(uuid4()),
        organization_name="Colombo North Clinic",
        distance_km=3.2,
        next_slot_id=slot_id,
        next_slot_at=at,
        fee_lkr=2500,
        languages=(Language.SI, Language.EN),
        reason_key="doctor.nearest_soonest",
    )


# Amma's dizziness, as she might say it.
AMMA_EN = {
    "first": "I feel dizzy since yesterday",
    "since": "about 2 days",
    "steps": [question("dizziness_since", "duration"), result(Urgency.ROUTINE)],
}
AMMA_SI = {
    "first": "මට ඔලුව කැරකෙනවා",
    "since": "දින 2",
    "steps": [question("dizziness_since", "duration"), result(Urgency.ROUTINE)],
}
