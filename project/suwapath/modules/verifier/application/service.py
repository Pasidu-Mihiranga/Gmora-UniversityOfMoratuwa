"""The verifier port (VER-1 to VER-10): stage 1 checks, stage 2 Jev, the VER-4 policy, stored
verdicts and the human review queue.

Every verdict is stored and announced as `verifier.verdict_issued` (VER-6): the author agent is
resumed on `revise`, and delivery acts on a human decision. `escalate` creates a review item for
the doctor (clinical messages) or the patient (messages for their family), never an admin (VER-5).
A doctor's reply draft is not queued here: the doctor's own approval is its human review (AGT-30).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select, tuple_

from suwapath.contracts.agents import AgentMessage, MessageIntent
from suwapath.contracts.common import Page
from suwapath.contracts.enums import AgentKind, DecidedBy, Role, VerdictKind
from suwapath.contracts.errors import AccessDenied, NotFound, ValidationFailed
from suwapath.contracts.events.catalog import VerdictIssued
from suwapath.contracts.ids import CaseId, MessageId, PatientId, ResourceId, UserId
from suwapath.contracts.ports.notifications import NotificationDraft
from suwapath.contracts.ports.verifier import (
    CheckResult,
    ReviewItem,
    Verdict,
    VerificationContext,
    VerifierCheck,
)
from suwapath.contracts.principal import Principal
from suwapath.modules.verifier.adapters.models import ReviewRow, VerdictRow
from suwapath.modules.verifier.application.model_checks import questions, state
from suwapath.modules.verifier.domain.policy import (
    RISKS,
    VERDICT_QUESTION,
    Decision,
    ModelOutcome,
    Thresholds,
    decide,
    is_fast_lane,
)
from suwapath.platform.gateway.types import ChoiceAnswer, DecisionResult, NoulAnswer, Question
from suwapath.platform.ids import new_id
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

logger = logging.getLogger("suwapath.verifier")
DecideFn = Callable[[list[str], dict[str, Question]], Awaitable[DecisionResult]]
REVIEWS = "verifier_reviews"
DOCTOR_SIDE = frozenset({AgentKind.DOCTOR_ASSISTANT.value, Role.DOCTOR.value})
MAX_PAGE = 50
DEFAULT_THRESHOLDS = Thresholds()


class SqlVerifier:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        checks: Callable[[], list[VerifierCheck]],
        ports: Callable[[], Mapping[str, Any]],
        decide_fn: Callable[[], DecideFn | None],
        thresholds: Thresholds = DEFAULT_THRESHOLDS,
    ) -> None:
        self._uow = uow_factory
        self._checks = checks
        self._ports = ports
        self._decide_fn = decide_fn
        self.thresholds = thresholds

    # ----- check (VER-1 to VER-4, VER-8 to VER-10) -----------------------------------------------

    async def check(self, message: AgentMessage, context: VerificationContext) -> Verdict:
        results = tuple(check.run(message, context) for check in self._checks())
        async with self._uow.join() as uow:
            outcome, result = await self._model(uow, message, context, results)
            decision = decide(message, results, outcome, self.thresholds)
            verdict = Verdict(
                message_id=message.message_id,
                verdict=decision.verdict,
                decided_by=decision.decided_by,
                checks=(*results, *_policy_result(decision)),
                model_probabilities=_probabilities(result),
                model_checks=_model_checks(outcome),
                model_id=result.model if result else None,
                cost_usd=result.usage.cost_usd if result else 0.0,
                latency_ms=result.latency_ms if result else 0,
                at=uow.clock.now(),
            )
            await self._store(uow, verdict, message.case_id)
            if decision.verdict is VerdictKind.ESCALATE:
                await self._queue_review(uow, message, decision.codes)
            return verdict

    async def _model(
        self,
        uow: UnitOfWork,
        message: AgentMessage,
        context: VerificationContext,
        results: tuple[CheckResult, ...],
    ) -> tuple[ModelOutcome, DecisionResult | None]:
        if any(not r.passed for r in results) or is_fast_lane(message):
            return ModelOutcome("skipped"), None  # no model cost for a message rules decide
        decide_fn = self._decide_fn()
        if decide_fn is None:
            return ModelOutcome("not_configured"), None
        try:
            result = await decide_fn(state(message, context), questions())
        except Exception as exc:  # fail closed (VER-9): any failure holds the message
            logger.warning("verifier model failed", extra={"error_code": type(exc).__name__})
            return ModelOutcome("failed", held_for=await self._held_for(uow, message)), None
        return _outcome(result), result

    async def _held_for(self, uow: UnitOfWork, message: AgentMessage) -> timedelta:
        first = (
            await uow.session.execute(
                select(func.min(VerdictRow.at)).where(
                    VerdictRow.message_id == message.message_id,
                    VerdictRow.verdict == VerdictKind.HOLD.value,
                )
            )
        ).scalar()
        return uow.clock.now() - first if first is not None else timedelta(0)

    async def _store(self, uow: UnitOfWork, verdict: Verdict, case_id: CaseId | None) -> None:
        uow.session.add(
            VerdictRow(
                id=new_id(),
                message_id=verdict.message_id,
                case_id=case_id,
                verdict=verdict.verdict.value,
                decided_by=verdict.decided_by.value,
                checks=[c.model_dump(mode="json") for c in verdict.checks],
                model_probabilities=verdict.model_probabilities,
                model_checks=verdict.model_checks,
                model_id=verdict.model_id,
                cost_usd=verdict.cost_usd,
                latency_ms=verdict.latency_ms,
                at=verdict.at,
            )
        )
        uow.publish(
            VerdictIssued(
                message_id=verdict.message_id,
                verdict=verdict.verdict,
                decided_by=verdict.decided_by,
                case_id=case_id,
            ),
            aggregate_id=str(verdict.message_id),
            correlation_id=case_id,
        )

    # ----- the review queue (VER-5) --------------------------------------------------------------

    async def _queue_review(
        self, uow: UnitOfWork, message: AgentMessage, codes: tuple[str, ...]
    ) -> None:
        if message.intent is MessageIntent.REPLY_DRAFT:
            return  # the doctor's approval is the human review (AGT-30)
        directory = self._ports()["agent_directory"]
        patient = PatientId(await directory.case_patient(message.case_id))
        reviewer, role = await self._reviewer(message, patient)
        if reviewer is None:
            logger.warning(
                "no reviewer for an escalated message", extra={"intent": message.intent.value}
            )
            return  # stays escalated and undelivered (fail closed)
        review_id = new_id()
        preview = json.dumps(
            {"intent": message.intent.value, "recipient": message.recipient, "body": message.body},
            ensure_ascii=False,
            sort_keys=True,
        )
        uow.session.add(
            ReviewRow(
                id=review_id,
                message_id=message.message_id,
                reviewer_id=reviewer,
                reviewer_role=role,
                case_id=message.case_id,
                patient_id=patient,
                preview_enc=await self._seal(patient, review_id, preview),
                reason_codes=[code[:40] for code in codes],
                created_at=uow.clock.now(),
            )
        )
        await self._ports()["notifications"].notify(
            NotificationDraft(
                user_id=reviewer,
                kind="review_required",
                template_key=f"review.{message.intent.value}",
                params={"review_id": str(review_id)},
                link=f"/reviews/{review_id}",
            )
        )

    async def _reviewer(
        self, message: AgentMessage, patient: PatientId
    ) -> tuple[UserId | None, Literal["doctor", "patient"]]:
        """The doctor for clinical messages, the patient for everything else (VER-5)."""
        directory = self._ports()["agent_directory"]
        if message.sender.value in DOCTOR_SIDE or message.recipient in DOCTOR_SIDE:
            if message.recipient == Role.DOCTOR.value and message.acting_for is not None:
                return message.acting_for, "doctor"
            found = await directory.doctor_case(patient)
            doctor = found[1] if found and found[0] == message.case_id else None
            return doctor, "doctor"
        try:
            return UserId(await directory.patient_user(patient)), "patient"
        except NotFound:
            return None, "patient"

    async def reviews(
        self, principal: Principal, cursor: str | None, limit: int
    ) -> Page[ReviewItem]:
        reviewer = _reviewer_of(principal)
        limit = max(1, min(limit, MAX_PAGE))
        async with self._uow.join() as uow:
            query = select(ReviewRow).where(
                ReviewRow.reviewer_id == reviewer, ReviewRow.status == "pending"
            )
            if cursor is not None:
                after = await self._own_review(uow, reviewer, cursor)
                query = query.where(
                    tuple_(ReviewRow.created_at, ReviewRow.id) < tuple_(after.created_at, after.id)
                )
            rows = list(
                (
                    await uow.session.execute(
                        query.order_by(ReviewRow.created_at.desc(), ReviewRow.id.desc()).limit(
                            limit + 1
                        )
                    )
                ).scalars()
            )
            items = [
                ReviewItem(
                    id=ResourceId(row.id),
                    message_id=MessageId(row.message_id),
                    reviewer_role=row.reviewer_role,
                    preview=await self._open(PatientId(row.patient_id), row.id, row.preview_enc),
                    reason_codes=tuple(row.reason_codes),
                    created_at=row.created_at,
                )
                for row in rows[:limit]
            ]
        return Page(items=items, next_cursor=str(items[-1].id) if len(rows) > limit else None)

    async def decide_review(
        self,
        principal: Principal,
        review_id: ResourceId,
        decision: Literal["send", "edit", "discard"],
        edited_text: str | None,
    ) -> Verdict:
        reviewer = _reviewer_of(principal)
        if decision == "edit":
            # Editing needs the author's text to be re-verified; not offered for these messages.
            raise ValidationFailed("this message can be sent or discarded")
        async with self._uow.join() as uow:
            row = await self._own_review(uow, reviewer, str(review_id))
            if row.status != "pending":
                raise NotFound()  # already decided: nothing left to act on
            now = uow.clock.now()
            row.status = "sent" if decision == "send" else "discarded"
            row.decided_at = now
            verdict = Verdict(
                message_id=MessageId(row.message_id),
                verdict=VerdictKind.ALLOW if decision == "send" else VerdictKind.BLOCK,
                decided_by=DecidedBy.HUMAN,
                checks=(
                    CheckResult(
                        check="human_review", passed=decision == "send", codes=(row.reviewer_role,)
                    ),
                ),
                model_checks="not_run",
                at=now,
            )
            await self._store(uow, verdict, CaseId(row.case_id))
            return verdict

    async def _own_review(self, uow: UnitOfWork, reviewer: UserId, review_id: str) -> ReviewRow:
        try:
            row = await uow.session.get(ReviewRow, UUID(review_id))
        except ValueError:
            row = None
        if row is None or row.reviewer_id != reviewer:
            raise NotFound()  # never reveal another person's review (CON-11)
        return row

    async def _seal(self, patient: PatientId, row_id: UUID, text: str) -> bytes:
        cipher = self._ports().get("patient_cipher")
        if cipher is None:
            return text.encode()
        sealed: bytes = await cipher.encrypt(patient, REVIEWS, "preview_enc", row_id, text.encode())
        return sealed

    async def _open(self, patient: PatientId, row_id: UUID, sealed: bytes) -> str:
        cipher = self._ports().get("patient_cipher")
        if cipher is None:
            return sealed.decode()
        try:
            opened: bytes = await cipher.decrypt(patient, REVIEWS, "preview_enc", row_id, sealed)
        except NotFound:
            return ""  # the patient's key was destroyed (crypto-shredding)
        return opened.decode()


def _reviewer_of(principal: Principal) -> UserId:
    if principal.kind != "user" or principal.user_id is None:
        raise AccessDenied()
    if principal.role not in (Role.PATIENT, Role.DOCTOR):
        raise AccessDenied()  # system admins never review content (VER-5)
    return UserId(principal.user_id)


def _outcome(result: DecisionResult) -> ModelOutcome:
    risks = {
        name: answer.probability
        for name, answer in result.answers.items()
        if name in RISKS and isinstance(answer, NoulAnswer)
    }
    choice = result.answers.get(VERDICT_QUESTION)
    if isinstance(choice, ChoiceAnswer):
        return ModelOutcome(
            "answered", risks=risks, choice=choice.choice, confidence=choice.confidence
        )
    return ModelOutcome("answered", risks=risks)


def _probabilities(result: DecisionResult | None) -> dict[str, float]:
    if result is None:
        return {}
    found: dict[str, float] = {}
    for name, answer in result.answers.items():
        if isinstance(answer, NoulAnswer):
            found[name] = answer.probability
        else:
            found[f"{name}:{answer.choice}"] = answer.confidence
    return found


def _model_checks(outcome: ModelOutcome) -> Literal["run", "not_run", "failed"]:
    if outcome.state == "answered":
        return "run"
    return "failed" if outcome.state == "failed" else "not_run"


def _policy_result(decision: Decision) -> tuple[CheckResult, ...]:
    """The policy's own reasons (rules only, fast lane, held, revised twice), for the record."""
    codes = tuple(c for c in decision.codes if ":" not in c)
    return (CheckResult(check="policy", passed=True, codes=codes),) if codes else ()
