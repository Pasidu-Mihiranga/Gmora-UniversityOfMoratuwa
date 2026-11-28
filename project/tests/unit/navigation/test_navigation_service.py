"""The navigation port over the case's facts, with the real safety rules (NAV-1, NAV-4, NAV-6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from suwapath.contracts.cases import CaseFact, CaseState
from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import CaseStatus, Language, Specialty, Urgency
from suwapath.contracts.ids import CaseId, PatientId, ResourceId
from suwapath.contracts.ports.safety import PatientContext
from suwapath.modules.navigation.application.service import KbNavigationService
from suwapath.modules.navigation.domain.kb import KnowledgeBase
from suwapath.modules.safety.application.service import RuleSafetyService
from suwapath.platform.clock import FixedClock

KB = KnowledgeBase.load()
SAFETY = RuleSafetyService.from_content()
ADULT = PatientContext(age_years=35, sex="female")
SOURCE = ResourceId(uuid4())


class Cases:
    def __init__(self, language: Language = Language.EN) -> None:
        self.state = CaseState(
            id=CaseId(uuid4()),
            patient_id=PatientId(uuid4()),
            status=CaseStatus.INTAKE,
            language=language,
            version=1,
        )

    async def get(self, principal: Any, case_id: CaseId) -> CaseState:
        return self.state

    def record(self, key: str, value: str, present: bool = True) -> None:
        fact = CaseFact(key=key, present=present, value=value, provenance_ids=(SOURCE,))
        self.state = self.state.model_copy(update={"facts": (*self.state.facts, fact)})


def service(cases: Cases) -> KbNavigationService:
    return KbNavigationService(
        KB,
        lambda: {"cases": cases, "safety": SAFETY},
        FixedClock(datetime(2026, 11, 28, 4, 0, tzinfo=UTC)),
    )


def said(text: str) -> UntrustedText:
    return UntrustedText(value=text, source="patient_message")


async def test_an_emergency_in_the_first_message_stops_at_once() -> None:
    cases = Cases()
    step = await service(cases).start(
        None, cases.state.id, said("I can't breathe"), ADULT, Language.EN
    )  # type: ignore[arg-type]
    assert step.emergency and step.question is None and step.result is None


async def test_the_first_step_returns_new_findings_and_a_question_with_its_text() -> None:
    cases = Cases()
    step = await service(cases).start(
        None, cases.state.id, said("I have a cough and fever"), ADULT, Language.EN
    )  # type: ignore[arg-type]
    assert {f.concept for f in step.findings} == {"cough", "fever"}
    assert step.question is not None and step.question.text
    assert step.question.red_flag_screen  # fever: breathing first (NAV-1)


async def test_facts_already_on_the_case_are_not_returned_again() -> None:
    cases = Cases()
    cases.record("cough", "mentioned")
    step = await service(cases).start(
        None, cases.state.id, said("still the cough, now fever"), ADULT, Language.EN
    )  # type: ignore[arg-type]
    assert {f.concept for f in step.findings} == {"fever"}


async def test_sinhala_cases_get_sinhala_questions_and_options() -> None:
    cases = Cases(Language.SI)
    cases.record("dizzy", "mentioned")
    step = await service(cases).answer(
        None, cases.state.id, "fever", "no", PatientContext(age_years=70)
    )  # type: ignore[arg-type]
    assert step.question is not None and step.question.answer_type == "choice"
    assert step.question.concept == "dizzy_kind"
    assert "හැම දෙයක්ම කැරකෙනවා වගේ" in step.question.choices


async def test_a_spinning_answer_routes_by_the_reviewed_dizziness_rule() -> None:
    cases = Cases()
    cases.record("dizzy", "mentioned")
    step = await service(cases).answer(
        None, cases.state.id, "dizzy_kind", "Everything is spinning", PatientContext(age_years=70)
    )  # type: ignore[arg-type]
    assert [f.concept for f in step.findings] == ["vertigo"]


async def test_yes_to_a_red_flag_concept_is_screened_by_the_safety_rules() -> None:
    cases = Cases()
    cases.record("chest_pain", "mentioned")
    older = PatientContext(age_years=58, sex="male")
    step = await service(cases).answer(None, cases.state.id, "pain_spreading", "yes", older)  # type: ignore[arg-type]
    assert step.emergency  # RF-CARD-001: chest pain spreading


async def test_red_flags_on_the_case_set_the_floor_of_the_result() -> None:
    cases = Cases()
    cases.record("red_flag.RF-RESP-002", "urgent_24h")
    for concept in ("runny_nose", "sore_throat"):
        cases.record(concept, "mentioned")
    for concept in KB.questions:
        if concept not in ("runny_nose", "sore_throat"):
            cases.record(concept, "no", present=False)
    result = await service(cases).result(None, cases.state.id)  # type: ignore[arg-type]
    assert result is not None
    assert result.urgency is Urgency.URGENT_24H and result.urgency_floor is Urgency.URGENT_24H


async def test_the_result_never_carries_a_condition_group() -> None:
    cases = Cases()
    for concept in ("chest_pain", "pain_spreading", "sweating", "breathless"):
        cases.record(concept, "mentioned")
    result = await service(cases).result(None, cases.state.id)  # type: ignore[arg-type]
    assert result is not None and result.primary_specialty is Specialty.CARDIOLOGY
    text = json.dumps(result.model_dump(mode="json")).casefold()
    for group in KB.groups:
        assert group.id not in text  # NAV-2: hypotheses never leave the engine
    assert result.kb_version == KB.version and result.rules_version.startswith("rules-v1")
    assert all(f.provenance_id == SOURCE for f in result.findings)


async def test_no_navigation_yet_means_no_result() -> None:
    cases = Cases()
    assert await service(cases).result(None, cases.state.id) is None  # type: ignore[arg-type]


async def test_findings_from_different_turns_combine_into_a_red_flag() -> None:
    cases = Cases()
    cases.record("chest_pain", "mentioned")  # said earlier
    step = await service(cases).start(
        None, cases.state.id, said("now I am sweating a lot"), ADULT, Language.EN
    )  # type: ignore[arg-type]
    assert step.emergency  # RF-CARD-001 across two messages


async def test_an_ordinary_neck_ache_with_fever_is_not_an_emergency() -> None:
    cases = Cases()
    step = await service(cases).start(
        None, cases.state.id, said("fever and my neck hurts"), ADULT, Language.EN
    )  # type: ignore[arg-type]
    assert not step.emergency
    for finding in step.findings:  # the companion records them (NAV-4)
        cases.record(finding.concept, "mentioned")
    assert step.question is not None and step.question.red_flag_screen
    asked = {step.question.concept}
    while step.question is not None and step.question.red_flag_screen and len(asked) < 4:
        cases.record(step.question.concept, "no", present=False)
        step = await service(cases).answer(None, cases.state.id, step.question.concept, "no", ADULT)  # type: ignore[arg-type]
        if step.question is not None:
            asked.add(step.question.concept)
    assert "stiff_neck" in asked  # the precise question is still asked (RF-NEURO-002)
