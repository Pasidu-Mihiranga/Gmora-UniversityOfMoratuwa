"""The guardian agent (spec 05 section 4): acts for the guardian, limited by the patient's consent.

Policy:
- score the week (rules for clear weeks, Jev for the unclear middle, `week_score`);
- `stable`, `watch`: no contact (alert fatigue is a harm, H5);
- `concerning`: check in with the patient first; alert the guardian with the result, or when the
  patient has not answered by the request deadline plus the follow-up wait;
- `urgent`: alert the guardian at once;
- a guardian's own alert rule acts as a trigger and means at least `concerning` (CARE-8);
- a red flag stands the routine check-in down: the companion sends the emergency alert.

Alerts carry adherence facts only (the guardian's consented category); delivery re-checks consent
at send time (SEC-13). The patient's words never reach the guardian, only a structured result.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from suwapath.contracts.agents import (
    AgentJob,
    AgentMessage,
    AgentMessageDraft,
    AgentResult,
    AgentSpec,
    MessageIntent,
    WaitCondition,
)
from suwapath.contracts.cases import CaseEventDraft, CaseEventKind
from suwapath.contracts.enums import AgentKind, DataCategory, Purpose, Role, Urgency
from suwapath.contracts.errors import CapabilityMissing, NotFound, ProviderUnavailable
from suwapath.contracts.ids import MessageId, PatientId
from suwapath.contracts.ports.care import AdherenceSummary
from suwapath.modules.agents.application.context import AgentContext
from suwapath.modules.agents.application.messages import message_id_for
from suwapath.modules.agents.application.runtime import CHECKIN_FOLLOWUP_TIMER
from suwapath.modules.agents.domain.week_score import (
    CRITERIA,
    INSTRUCTIONS,
    WeekScore,
    combine,
    decision_state,
    needs_judgement,
    rules_score,
)
from suwapath.platform.gateway.types import ChoiceAnswer, ChoiceQuestion

FOLLOWUP_AFTER = timedelta(hours=2)  # spec 03 section 6, `guardian_checkin_followup`
SCOPE = frozenset({DataCategory.ADHERENCE})
URGENT_CHECKIN = (Urgency.URGENT_24H, Urgency.EMERGENCY)


class GuardianAgent:
    spec = AgentSpec(
        kind=AgentKind.GUARDIAN_AGENT,
        acts_for_role=Role.GUARDIAN,
        purposes=frozenset({Purpose.GUARDIAN_MONITORING, Purpose.EMERGENCY}),
        data_scope=frozenset(
            {DataCategory.ADHERENCE, DataCategory.MEDICATIONS, DataCategory.APPOINTMENTS}
        ),
        tools=frozenset({"care.adherence_summary", "cases.read_summary", "cases.append"}),
        may_message=frozenset({AgentKind.PATIENT_COMPANION.value, Role.GUARDIAN.value}),
        max_steps=6,
        prompt_version="guardian-1",
    )

    async def handle(self, job: AgentJob, ctx: AgentContext) -> AgentResult:
        data = job.input
        trigger = data.get("trigger")
        if trigger in ("dose_missed", "alert_rule") and job.patient_id is not None:
            return await self._assess(job, ctx, job.patient_id)
        if trigger == "red_flag" and job.patient_id is not None:
            return await self._stand_down(ctx, job.patient_id)
        if trigger == "checkin_followup" and data.get("request_id"):
            return await self._followup(ctx, MessageId(UUID(data["request_id"])))
        if "message_id" in data:
            return await self._on_result(ctx, MessageId(UUID(data["message_id"])))
        if "timeout_of" in data:
            return await self._on_timeout(job, ctx, MessageId(UUID(data["timeout_of"])))
        return AgentResult(status="done", reason="nothing_to_do")

    # ----- scoring and the first move ---------------------------------------------------------

    async def _assess(self, job: AgentJob, ctx: AgentContext, patient: PatientId) -> AgentResult:
        summary = await ctx.adherence(patient)
        if summary is None:
            return AgentResult(status="done", reason="no_adherence_access")
        score, decided_by = await self._score(ctx, summary)
        if job.input.get("trigger") == "alert_rule":
            if job.input.get("rule_type") == "no_checkin_response":
                score = WeekScore.URGENT if score is WeekScore.URGENT else WeekScore.CONCERNING
            elif score.rank < WeekScore.CONCERNING.rank:
                score, decided_by = WeekScore.CONCERNING, "guardian_rule"
        reason = f"week_{score.value}_{decided_by}"
        if score in (WeekScore.STABLE, WeekScore.WATCH):
            return AgentResult(status="done", reason=reason)
        day = ctx.clock.now().date().isoformat()
        if score is WeekScore.URGENT:
            key = f"alert-urgent:{patient}:{day}"
            if await ctx.message(message_id_for(key)) is not None:
                return AgentResult(status="done", reason="already_alerted_today")
            await ctx.open_case(patient, "missed_doses")
            return AgentResult(
                status="done",
                reason=reason,
                case_events=(_branch("alerted", "urgent"),),
                messages=(_alert("urgent", _facts(summary), "alert.call_patient_now", key),),
            )
        key = f"checkin:{patient}:{day}"
        if await ctx.message(message_id_for(key)) is not None:
            return AgentResult(status="done", reason="already_checking_in")
        await ctx.open_case(patient, "missed_doses")
        facts = _facts(summary)
        check_in = AgentMessageDraft(
            intent=MessageIntent.CHECK_IN_WITH_PATIENT,
            recipient=AgentKind.PATIENT_COMPANION.value,
            body={
                "reason": "alert_rule"
                if job.input.get("trigger") == "alert_rule"
                else "missed_doses",
                "slots_missed": facts["slots_missed"],
                "missed_count": facts["missed_count"],
            },
            consent_scope=SCOPE,
            dedupe_hint=key,  # one check-in per patient per day
        )
        return AgentResult(
            status="waiting",
            reason=reason,
            case_events=(_branch("checking_in", None),),
            messages=(check_in,),
            wait_for=WaitCondition(kind="reply", ref=str(message_id_for(key))),
        )

    async def _score(self, ctx: AgentContext, summary: AdherenceSummary) -> tuple[WeekScore, str]:
        rules = rules_score(summary)
        if not needs_judgement(rules) or not ctx.model.can_decide:
            return rules, "rules"
        question = ChoiceQuestion(
            instructions=INSTRUCTIONS, criteria={s.value: CRITERIA[s] for s in WeekScore}
        )
        try:
            result = await ctx.model.decide(decision_state(summary), {"week": question})
        except (ProviderUnavailable, CapabilityMissing):
            return rules, "rules"  # no decision model right now: the rules still protect
        answer = result.answers.get("week")
        if not isinstance(answer, ChoiceAnswer):
            return rules, "rules"
        return combine(rules, answer.choice, answer.confidence)

    # ----- after the check-in ------------------------------------------------------------------

    async def _on_result(self, ctx: AgentContext, message_id: MessageId) -> AgentResult:
        answer = await ctx.message(message_id)
        if (
            answer is None
            or answer.intent is not MessageIntent.CHECK_IN_RESULT
            or answer.in_reply_to is None
        ):
            return AgentResult(status="done", reason="not_a_check_in_result")
        request = await ctx.message(answer.in_reply_to)
        if request is None:
            return AgentResult(status="done", reason="request_not_found")
        await ctx.uow.cancel_timer(_followup_key(request.message_id))
        if not await _still_consented(ctx):
            return AgentResult(status="done", reason="consent_withdrawn")
        patient_ok = str(answer.body.get("patient_ok", "unclear"))
        urgency = answer.body.get("urgency")
        if urgency in {u.value for u in URGENT_CHECKIN}:
            level, action = "urgent", "alert.call_patient_now"
        elif patient_ok == "yes":
            level, action = "info", "alert.no_action"
        else:
            level, action = "concern", "alert.call_patient"
        facts = {**_request_facts(request), "patient_ok": patient_ok}
        return AgentResult(
            status="done",
            case_events=(_branch("alerted", level),),
            messages=(_alert(level, facts, action, f"alert:{request.message_id}"),),
        )

    async def _on_timeout(
        self, job: AgentJob, ctx: AgentContext, request_id: MessageId
    ) -> AgentResult:
        """The request deadline passed: give the patient more time before telling the guardian."""
        if await ctx.has_answer(request_id):
            return AgentResult(status="done", reason="answered_in_time")
        request = await ctx.message(request_id)
        if request is None or job.patient_id is None or job.acting_for is None:
            return AgentResult(status="done", reason="request_not_found")
        due = ctx.clock.now() + FOLLOWUP_AFTER
        await ctx.uow.schedule_timer(
            kind=CHECKIN_FOLLOWUP_TIMER,
            due_at=due,
            ref_type="agent_message",
            ref_id=str(request_id),
            dedupe_key=_followup_key(request_id),
            payload={
                "patient_id": str(job.patient_id),
                "guardian_id": str(job.acting_for),
                "case_id": str(request.case_id),
                "request_id": str(request_id),
                "trace_id": job.trace_id,
            },
        )
        return AgentResult(
            status="waiting",
            reason="waiting_for_patient",
            wait_for=WaitCondition(kind="timer", ref=_followup_key(request_id), until=due),
        )

    async def _followup(self, ctx: AgentContext, request_id: MessageId) -> AgentResult:
        if await ctx.has_answer(request_id):
            return AgentResult(status="done", reason="answered_in_time")
        request = await ctx.message(request_id)
        if request is None:
            return AgentResult(status="done", reason="request_not_found")
        if not await _still_consented(ctx):
            return AgentResult(status="done", reason="consent_withdrawn")
        facts = {**_request_facts(request), "patient_ok": "no_response"}
        return AgentResult(
            status="done",
            case_events=(_branch("alerted", "concern"),),
            messages=(_alert("concern", facts, "alert.call_patient", f"alert:{request_id}"),),
        )

    async def _stand_down(self, ctx: AgentContext, patient: PatientId) -> AgentResult:
        """Red flag: no routine 'are you all right' now; the companion sends the emergency alert."""
        day = ctx.clock.now().date().isoformat()
        await ctx.uow.cancel_timer(_followup_key(message_id_for(f"checkin:{patient}:{day}")))
        return AgentResult(status="done", reason="emergency_alert_by_companion")


async def _still_consented(ctx: AgentContext) -> bool:
    """Consent wins (conflict rules): if the patient withdrew the guardian's access since the
    check-in began, the case is no longer visible and nothing is sent."""
    try:
        await ctx.cases.get()
    except NotFound:
        return False
    return True


def _facts(summary: AdherenceSummary) -> dict[str, Any]:
    days = sorted(summary.days, key=lambda d: d.day)
    recent = days[-2:]
    slots = sorted({s.value for d in recent for s in d.missed_slots})
    return {"missed_count": sum(d.missed for d in recent), "slots_missed": slots}


def _request_facts(request: AgentMessage) -> dict[str, Any]:
    return {
        "missed_count": int(request.body.get("missed_count", 0)),
        "slots_missed": list(request.body.get("slots_missed", [])),
    }


def _alert(level: str, facts: dict[str, Any], action: str, key: str) -> AgentMessageDraft:
    return AgentMessageDraft(
        intent=MessageIntent.GUARDIAN_ALERT,
        recipient=Role.GUARDIAN.value,
        body={"level": level, "facts": facts, "suggested_action_key": action},
        consent_scope=SCOPE,
        dedupe_hint=key,  # one alert per check-in, or per urgent day
    )


def _branch(status: str, level: str | None) -> CaseEventDraft:
    return CaseEventDraft(
        kind=CaseEventKind.GUARDIAN_UPDATED,
        data={"status": status, "last_alert_level": level},
        actor=AgentKind.GUARDIAN_AGENT.value,
    )


def _followup_key(request_id: MessageId) -> str:
    return f"followup:{request_id}"
