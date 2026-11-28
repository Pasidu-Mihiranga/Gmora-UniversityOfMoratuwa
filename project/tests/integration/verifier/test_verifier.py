"""The verifier on Postgres (M16): verdicts stored and announced, rules-only escalation to the right
human, Jev answers, fail closed with hold then escalate, the emergency fast lane, and a review
queue that only its reviewer can see and decide."""

from __future__ import annotations

import base64
import json
import os
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from suwapath.contracts.agents import AgentMessage, MessageIntent
from suwapath.contracts.enums import (
    AgentKind,
    DataCategory,
    DecidedBy,
    MessageKind,
    PrincipalKind,
    Purpose,
    Role,
    VerdictKind,
)
from suwapath.contracts.errors import AccessDenied, NotFound, ValidationFailed
from suwapath.contracts.ids import CaseId, MessageId, PatientId, TraceId, UserId
from suwapath.contracts.ports.consent import AccessDecision
from suwapath.contracts.ports.verifier import VerificationContext
from suwapath.contracts.principal import Principal
from suwapath.contracts.testing.doubles_core import InMemoryNotificationService
from suwapath.modules.record.application.keys import SqlPatientCipher
from suwapath.modules.safety.application.service import RuleSafetyService
from suwapath.modules.verifier.adapters.models import ReviewRow, VerdictRow
from suwapath.modules.verifier.application.service import SqlVerifier
from suwapath.modules.verifier.domain.checks import default_checks
from suwapath.platform.clock import FixedClock
from suwapath.platform.crypto import DekCache, LocalKeyProvider
from suwapath.platform.gateway.types import (
    ChoiceAnswer,
    DecisionResult,
    NoulAnswer,
    Question,
    Usage,
)
from suwapath.platform.models import OutboxRow
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
TRACE = TraceId("9" * 32)
ALLOWED = AccessDecision(allowed=True, reason="ok", policy_version="t")
SAFETY = RuleSafetyService.from_content()


class Directory:
    def __init__(self) -> None:
        self.patient, self.amma = PatientId(uuid4()), UserId(uuid4())
        self.doctor, self.son, self.case = UserId(uuid4()), UserId(uuid4()), CaseId(uuid4())

    async def case_patient(self, case_id: CaseId) -> PatientId:
        return self.patient

    async def patient_user(self, patient_id: PatientId) -> UserId:
        return self.amma

    async def doctor_case(self, patient_id: PatientId) -> tuple[CaseId, UserId] | None:
        return self.case, self.doctor


class Jev:
    """A scripted decision model: answers, or fails like a provider outage."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self.choice, self.confidence = "allow", 0.95
        self.risks: dict[str, float] = {}

    async def __call__(self, state: list[str], questions: dict[str, Question]) -> DecisionResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        assert set(questions) >= {"implies_diagnosis", "verdict"}
        answers: dict[str, Any] = {
            name: NoulAnswer(probability=self.risks.get(name, 0.05))
            for name in questions
            if name != "verdict"
        }
        answers["verdict"] = ChoiceAnswer(
            choice=self.choice,
            probabilities={self.choice: self.confidence},
            confidence=self.confidence,
        )
        return DecisionResult(
            answers=answers,
            usage=Usage(input_tokens=200, output_tokens=10, cost_usd=0.0004),
            model="jev-test",
            latency_ms=120,
        )


class Bench:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: FixedClock, model: bool) -> None:
        self.uow_factory, self.clock = uow_factory, clock
        self.directory = Directory()
        self.notifications = InMemoryNotificationService(now=clock.now)
        provider = LocalKeyProvider(base64.b64encode(os.urandom(32)).decode())
        cipher = SqlPatientCipher(uow_factory, lambda: provider, DekCache())
        self.jev = Jev()
        ports = {
            "agent_directory": self.directory,
            "notifications": self.notifications,
            "patient_cipher": cipher,
        }
        self.verifier = SqlVerifier(
            uow_factory,
            lambda: default_checks(SAFETY.check_wording),
            lambda: ports,
            (lambda: self.jev) if model else (lambda: None),
        )

    def message(self, intent: MessageIntent, **overrides: Any) -> AgentMessage:
        base: dict[str, Any] = {
            MessageIntent.GUARDIAN_ALERT: {
                "kind": MessageKind.ALERT,
                "sender": AgentKind.GUARDIAN_AGENT,
                "recipient": Role.GUARDIAN.value,
                "acting_for": self.directory.son,
                "body": {
                    "level": "concern",
                    "facts": {"missed_count": 2},
                    "suggested_action_key": "alert.call_patient",
                },
            },
            MessageIntent.CHECK_IN_RESULT: {
                "kind": MessageKind.ANSWER,
                "sender": AgentKind.PATIENT_COMPANION,
                "recipient": AgentKind.GUARDIAN_AGENT.value,
                "acting_for": self.directory.amma,
                "body": {"answered": True, "patient_ok": "yes"},
            },
            MessageIntent.EMERGENCY_ALERT: {
                "kind": MessageKind.ALERT,
                "sender": AgentKind.PATIENT_COMPANION,
                "recipient": Role.GUARDIAN.value,
                "acting_for": self.directory.amma,
                "body": {"patient_first_name": "Kamala", "contact_hint_key": "call_patient"},
            },
            MessageIntent.DOCTOR_BRIEF: {
                "kind": MessageKind.BRIEF,
                "sender": AgentKind.DOCTOR_ASSISTANT,
                "recipient": Role.DOCTOR.value,
                "acting_for": self.directory.doctor,
                "body": {"brief_id": str(uuid4())},
            },
            MessageIntent.REPLY_DRAFT: {
                "kind": MessageKind.DRAFT,
                "sender": AgentKind.DOCTOR_ASSISTANT,
                "recipient": Role.PATIENT.value,
                "acting_for": self.directory.doctor,
                "body": {
                    "text": "Please come today.",
                    "language": "en",
                    "based_on_notes_id": str(uuid4()),
                },
            },
        }[intent]
        mid = MessageId(uuid4())
        fields = {
            "message_id": mid,
            "conversation_id": mid,
            "case_id": self.directory.case,
            "intent": intent,
            "consent_scope": frozenset({DataCategory.ADHERENCE}),
            "hop": 1,
            "trace_id": TRACE,
            **base,
            **overrides,
        }
        return AgentMessage(**fields)

    async def check(self, message: AgentMessage, access: AccessDecision = ALLOWED) -> Any:
        async with self.uow_factory() as uow:
            verdict = await self.verifier.check(
                message, VerificationContext(recipient_access=access)
            )
            await uow.commit()
        return verdict

    def person(self, user: UserId, role: Role) -> Principal:
        return Principal(
            kind=PrincipalKind.USER,
            user_id=user,
            role=role,
            acting_for=user,
            purpose=Purpose.SELF_ACCESS,
            trace_id=TRACE,
        )

    async def rows(self, model: Any) -> list[Any]:
        async with self.uow_factory() as uow:
            found = list((await uow.session.execute(select(model))).scalars())
            uow.session.expunge_all()  # keep the loaded values after the block ends
            return found


@pytest.fixture
def rules_only(uow_factory: UnitOfWorkFactory, clock: FixedClock) -> Bench:
    return Bench(uow_factory, clock, model=False)


@pytest.fixture
def with_jev(uow_factory: UnitOfWorkFactory, clock: FixedClock) -> Bench:
    return Bench(uow_factory, clock, model=True)


# ----- stored and announced (VER-6) --------------------------------------------------------------


async def test_every_verdict_is_stored_and_announced(rules_only: Bench) -> None:
    verdict = await rules_only.check(rules_only.message(MessageIntent.CHECK_IN_RESULT))
    assert (verdict.verdict, verdict.decided_by, verdict.model_checks) == (
        VerdictKind.ALLOW,
        DecidedBy.RULES,
        "not_run",
    )
    [row] = await rules_only.rows(VerdictRow)
    assert (row.message_id, row.verdict, row.case_id) == (
        verdict.message_id,
        "allow",
        rules_only.directory.case,
    )
    events = [r for r in await rules_only.rows(OutboxRow) if r.type == "verifier.verdict_issued"]
    assert [e.payload["verdict"] for e in events] == ["allow"]


# ----- rules only (VER-10) and the review queue (VER-5) -------------------------------------------


async def test_rules_only_sends_a_family_alert_to_the_patient_to_review(rules_only: Bench) -> None:
    verdict = await rules_only.check(rules_only.message(MessageIntent.GUARDIAN_ALERT))
    assert verdict.verdict is VerdictKind.ESCALATE
    amma = rules_only.person(rules_only.directory.amma, Role.PATIENT)
    [item] = (await rules_only.verifier.reviews(amma, None, 10)).items
    assert item.reviewer_role == "patient" and item.reason_codes == ("rules_only",)
    assert json.loads(item.preview)["body"]["facts"] == {"missed_count": 2}
    [note] = rules_only.notifications.items
    assert (note.user_id, note.kind) == (rules_only.directory.amma, "review_required")
    [row] = await rules_only.rows(ReviewRow)
    assert b"missed_count" not in row.preview_enc  # sealed with the patient's key


async def test_a_doctor_brief_is_reviewed_by_its_doctor(rules_only: Bench) -> None:
    await rules_only.check(rules_only.message(MessageIntent.DOCTOR_BRIEF))
    doctor = rules_only.person(rules_only.directory.doctor, Role.DOCTOR)
    assert [
        i.reviewer_role for i in (await rules_only.verifier.reviews(doctor, None, 10)).items
    ] == ["doctor"]


async def test_a_reply_draft_is_not_queued_its_doctor_approves_it(rules_only: Bench) -> None:
    verdict = await rules_only.check(rules_only.message(MessageIntent.REPLY_DRAFT))
    assert verdict.verdict is VerdictKind.ESCALATE
    assert await rules_only.rows(ReviewRow) == []


async def test_only_the_reviewer_sees_and_decides_and_only_once(rules_only: Bench) -> None:
    message = rules_only.message(MessageIntent.GUARDIAN_ALERT)
    await rules_only.check(message)
    amma = rules_only.person(rules_only.directory.amma, Role.PATIENT)
    son = rules_only.person(rules_only.directory.son, Role.PATIENT)
    [item] = (await rules_only.verifier.reviews(amma, None, 10)).items
    assert (await rules_only.verifier.reviews(son, None, 10)).items == []
    with pytest.raises(NotFound):
        await rules_only.verifier.decide_review(son, item.id, "send", None)
    admin = rules_only.person(UserId(uuid4()), Role.HOSPITAL_ADMIN)
    with pytest.raises(AccessDenied):
        await rules_only.verifier.reviews(admin, None, 10)  # admins never review content
    with pytest.raises(ValidationFailed):
        await rules_only.verifier.decide_review(amma, item.id, "edit", "new text")
    async with rules_only.uow_factory() as uow:
        sent = await rules_only.verifier.decide_review(amma, item.id, "send", None)
        await uow.commit()
    assert (sent.verdict, sent.decided_by, sent.message_id) == (
        VerdictKind.ALLOW,
        DecidedBy.HUMAN,
        message.message_id,
    )
    with pytest.raises(NotFound):
        await rules_only.verifier.decide_review(amma, item.id, "discard", None)
    assert (await rules_only.verifier.reviews(amma, None, 10)).items == []
    events = [
        r.payload for r in await rules_only.rows(OutboxRow) if r.type == "verifier.verdict_issued"
    ]
    assert [(e["verdict"], e["decided_by"]) for e in events] == [
        ("escalate", "rules"),
        ("allow", "human"),
    ]


async def test_a_discarded_message_is_blocked_by_the_human(rules_only: Bench) -> None:
    await rules_only.check(rules_only.message(MessageIntent.GUARDIAN_ALERT))
    amma = rules_only.person(rules_only.directory.amma, Role.PATIENT)
    [item] = (await rules_only.verifier.reviews(amma, None, 10)).items
    async with rules_only.uow_factory() as uow:
        discarded = await rules_only.verifier.decide_review(amma, item.id, "discard", None)
        await uow.commit()
    assert (discarded.verdict, discarded.decided_by) == (VerdictKind.BLOCK, DecidedBy.HUMAN)


async def test_a_blocked_message_creates_no_review(rules_only: Bench) -> None:
    denied = AccessDecision(allowed=False, reason="no_grant", policy_version="t")
    verdict = await rules_only.check(rules_only.message(MessageIntent.GUARDIAN_ALERT), denied)
    assert verdict.verdict is VerdictKind.BLOCK
    assert await rules_only.rows(ReviewRow) == [] and rules_only.notifications.items == []


# ----- with Jev (VER-3, VER-4) --------------------------------------------------------------------


async def test_a_confident_jev_allow_reaches_the_person(with_jev: Bench) -> None:
    verdict = await with_jev.check(with_jev.message(MessageIntent.GUARDIAN_ALERT))
    assert (verdict.verdict, verdict.decided_by, verdict.model_checks, verdict.model_id) == (
        VerdictKind.ALLOW,
        DecidedBy.MODEL,
        "run",
        "jev-test",
    )
    assert verdict.cost_usd == 0.0004 and verdict.model_probabilities["implies_diagnosis"] == 0.05


async def test_jev_finding_a_diagnosis_returns_the_message(with_jev: Bench) -> None:
    with_jev.jev.risks = {"implies_diagnosis": 0.7}
    verdict = await with_jev.check(with_jev.message(MessageIntent.GUARDIAN_ALERT))
    assert verdict.verdict is VerdictKind.REVISE


async def test_rules_decide_first_and_jev_is_not_asked(with_jev: Bench) -> None:
    denied = AccessDecision(allowed=False, reason="no_grant", policy_version="t")
    await with_jev.check(with_jev.message(MessageIntent.GUARDIAN_ALERT), denied)
    assert with_jev.jev.calls == 0


async def test_the_emergency_alert_takes_the_fast_lane(with_jev: Bench) -> None:
    verdict = await with_jev.check(
        with_jev.message(
            MessageIntent.EMERGENCY_ALERT, consent_scope=frozenset({DataCategory.DEMOGRAPHICS})
        )
    )
    assert (verdict.verdict, with_jev.jev.calls) == (VerdictKind.ALLOW, 0)


# ----- fail closed (VER-9) ------------------------------------------------------------------------


async def test_jev_down_holds_the_message_then_a_human_decides_after_30_minutes(
    with_jev: Bench,
) -> None:
    with_jev.jev.fail = True
    message = with_jev.message(MessageIntent.CHECK_IN_RESULT)  # even agent-bound: never unchecked
    assert (await with_jev.check(message)).verdict is VerdictKind.HOLD
    with_jev.clock.advance(timedelta(minutes=10))
    held = await with_jev.check(message)
    assert (held.verdict, held.model_checks) == (VerdictKind.HOLD, "failed")
    with_jev.clock.advance(timedelta(minutes=21))
    assert (await with_jev.check(message)).verdict is VerdictKind.ESCALATE
