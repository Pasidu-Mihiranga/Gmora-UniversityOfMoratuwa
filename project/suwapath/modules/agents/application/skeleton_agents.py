"""Walking-skeleton agents: the guardian agent and the patient companion (check-in flow only).

Deterministic on purpose: this milestone proves coordination (bus, jobs, consent, verifier, timers,
dedupe, tracing), not reasoning. The full agents (A03, A04) replace these and add model calls.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from suwapath.contracts.agents import (
    AgentJob,
    AgentMessageDraft,
    AgentResult,
    AgentSpec,
    MessageIntent,
)
from suwapath.contracts.enums import AgentKind, DataCategory, Language, Purpose, Role
from suwapath.contracts.ids import MessageId, PatientId
from suwapath.modules.agents.application.context import AgentContext

MISSED_DOSE_WINDOW = timedelta(hours=48)
CONCERNING_MISSED = 2

CHECK_IN_TEXT = {
    Language.EN: "Hello. Some evening tablets were not marked. Are you feeling all right?",
    Language.SI: "ආයුබෝවන්. සවස බෙහෙත් සමහරක් සටහන් වී නැහැ. ඔබට සනීපද?",
}
OK_WORDS = ("ok", "fine", "yes", "good", "හොඳින්", "සනීප")
NOT_OK_WORDS = ("dizzy", "not ok", "not well", "pain", "කැරකෙනවා", "අමාරු")


class GuardianAgent:
    spec = AgentSpec(
        kind=AgentKind.GUARDIAN_AGENT,
        acts_for_role=Role.GUARDIAN,
        purposes=frozenset({Purpose.GUARDIAN_MONITORING}),
        data_scope=frozenset(
            {DataCategory.ADHERENCE, DataCategory.MEDICATIONS, DataCategory.APPOINTMENTS}
        ),
        tools=frozenset({"care.adherence_summary"}),
        may_message=frozenset({AgentKind.PATIENT_COMPANION.value, Role.GUARDIAN.value}),
        prompt_version="skeleton-1",
    )

    async def handle(self, job: AgentJob, ctx: AgentContext) -> AgentResult:
        data = job.input
        if data.get("trigger") == "dose_missed":
            patient = PatientId(UUID(data["patient_id"]))
            missed = await ctx.missed_doses(patient, MISSED_DOSE_WINDOW)
            if missed < CONCERNING_MISSED:
                return AgentResult(status="done", reason="below_threshold")
            await ctx.open_case(patient, "missed_doses")
            day = ctx.clock.now().date().isoformat()
            return AgentResult(
                status="waiting",
                messages=(
                    AgentMessageDraft(
                        intent=MessageIntent.CHECK_IN_WITH_PATIENT,
                        recipient=AgentKind.PATIENT_COMPANION.value,
                        body={
                            "reason": "missed_doses",
                            "slots_missed": ["evening"],
                            "missed_count": missed,
                        },
                        consent_scope=frozenset({DataCategory.ADHERENCE}),
                        dedupe_hint=f"checkin:{patient}:{day}",  # one check-in per patient per day
                    ),
                ),
            )
        if "message_id" in data:
            answer = await ctx.message(MessageId(UUID(data["message_id"])))
            if (
                answer is None
                or answer.intent is not MessageIntent.CHECK_IN_RESULT
                or answer.in_reply_to is None
            ):
                return AgentResult(status="done", reason="not_a_check_in_result")
            return await self._alert(
                ctx, answer.in_reply_to, answer.body.get("patient_ok", "unclear")
            )
        if "timeout_of" in data:
            request_id = MessageId(UUID(data["timeout_of"]))
            if await ctx.has_answer(request_id):
                return AgentResult(status="done", reason="answered_in_time")
            return await self._alert(ctx, request_id, "unclear")
        return AgentResult(status="done", reason="nothing_to_do")

    async def _alert(
        self, ctx: AgentContext, request_id: MessageId, patient_ok: str
    ) -> AgentResult:
        request = await ctx.message(request_id)
        missed = int(request.body.get("missed_count", 0)) if request else 0
        level = "info" if patient_ok == "yes" else "concern"
        return AgentResult(
            status="done",
            messages=(
                AgentMessageDraft(
                    intent=MessageIntent.GUARDIAN_ALERT,
                    recipient=Role.GUARDIAN.value,
                    body={
                        "level": level,
                        "facts": {"missed_count": missed, "patient_ok": patient_ok},
                        "suggested_action_key": "alert.call_patient"
                        if level == "concern"
                        else "alert.no_action",
                    },
                    consent_scope=frozenset({DataCategory.ADHERENCE}),
                    dedupe_hint=f"alert:{request_id}",  # one alert per check-in
                ),
            ),
        )


class CompanionAgent:
    spec = AgentSpec(
        kind=AgentKind.PATIENT_COMPANION,
        acts_for_role=Role.PATIENT,
        purposes=frozenset({Purpose.CARE_NAVIGATION}),
        data_scope=frozenset(DataCategory)
        - {DataCategory.SENSITIVE_SEXUAL_HEALTH, DataCategory.SENSITIVE_MENTAL_HEALTH},
        tools=frozenset({"cases.deliver"}),
        may_message=frozenset({AgentKind.GUARDIAN_AGENT.value}),
        prompt_version="skeleton-1",
    )

    async def handle(self, job: AgentJob, ctx: AgentContext) -> AgentResult:
        data = job.input
        if "message_id" in data:
            request = await ctx.message(MessageId(UUID(data["message_id"])))
            if request is None or request.intent is not MessageIntent.CHECK_IN_WITH_PATIENT:
                return AgentResult(status="done", reason="unsupported_request")
            patient = await ctx.deps.directory.case_patient(request.case_id)
            language = await ctx.deps.directory.language(patient)
            await ctx.deps.cases.deliver_communication(
                request.case_id, "patient", CHECK_IN_TEXT[language], False
            )
            return AgentResult(status="waiting", reason="asked_patient")
        if data.get("trigger") == "patient_message":
            request = await ctx.pending_request(MessageIntent.CHECK_IN_WITH_PATIENT)
            if request is None or ctx.case_id is None:
                return AgentResult(status="done", reason="no_open_check_in")
            reply = await ctx.deps.latest_patient_text(ctx.case_id)
            patient_ok = _classify(reply.value if reply else "")
            # The guardian side receives only a structured result, never the patient's words.
            return AgentResult(
                status="done",
                messages=(
                    AgentMessageDraft(
                        intent=MessageIntent.CHECK_IN_RESULT,
                        recipient=AgentKind.GUARDIAN_AGENT.value,
                        in_reply_to=request.message_id,
                        body={
                            "answered": True,
                            "patient_ok": patient_ok,
                            "consented_summary_keys": ["checkin.replied"],
                        },
                        consent_scope=frozenset({DataCategory.ADHERENCE}),
                        dedupe_hint=f"checkin-result:{request.message_id}",
                    ),
                ),
            )
        return AgentResult(status="done", reason="nothing_to_do")


def _classify(text: str) -> str:
    lowered = text.casefold()
    if any(w in lowered for w in NOT_OK_WORDS):
        return "no"
    if any(w in lowered for w in OK_WORDS):
        return "yes"
    return "unclear"
