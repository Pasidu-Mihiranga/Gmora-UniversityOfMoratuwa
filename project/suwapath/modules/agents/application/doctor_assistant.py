"""The doctor assistant (spec 05 section 4): acts for the treating doctor.

Two jobs:
- **The brief.** Rebuilt whenever something the doctor should know changes (a booking, a report,
  imaging, a record write, an answered question). Built from structured data only
  (`domain.brief`); clashes from `safety.check_clashes`. Facts the doctor needs that are missing
  are asked from the patient through the companion (`confirm_missing_fact`), once each.
- **The reply.** The doctor writes notes for the patient. Those words, unchanged, become a reply
  draft; the verifier checks it; the case asks the doctor to approve it (4 hour timer); the doctor
  approves, edits or rejects; only then is it delivered (AGT-30). This is a LangGraph graph with
  an interrupt at the approval, resumed by the doctor's decision. No model writes or summarises
  the doctor's words. Every node re-reads the case (EVT-20, EVT-21).
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from suwapath.contracts.agents import (
    AgentJob,
    AgentMessageDraft,
    AgentResult,
    AgentSpec,
    CompanionReply,
    MessageIntent,
)
from suwapath.contracts.briefs import Brief
from suwapath.contracts.cases import CaseEventDraft, CaseEventKind, CaseState
from suwapath.contracts.enums import (
    AgentKind,
    CaseStatus,
    DataCategory,
    MessageStatus,
    Purpose,
    Role,
    VerdictKind,
)
from suwapath.contracts.errors import NotFound
from suwapath.contracts.ids import MessageId, ResourceId
from suwapath.contracts.ports.consent import AccessDecision
from suwapath.contracts.ports.verifier import VerificationContext
from suwapath.contracts.suggestions import Suggestion
from suwapath.modules.agents.adapters import briefs
from suwapath.modules.agents.application import companion_text as copy
from suwapath.modules.agents.application import messages
from suwapath.modules.agents.application.context import AgentContext
from suwapath.modules.agents.domain.brief import build_lines, missing_facts, ordered
from suwapath.platform.ids import new_id

logger = logging.getLogger("suwapath.agents")
K = CaseEventKind
BRIEF_TRIGGERS = frozenset(
    {
        "appointment_booked",
        "reports.report_extracted",
        "imaging.screening_completed",
        "record.resource_written",
        "rebuild",
    }
)
BRIEF_SCOPE = frozenset(
    {
        DataCategory.CONDITIONS,
        DataCategory.MEDICATIONS,
        DataCategory.ALLERGIES,
        DataCategory.OBSERVATIONS,
    }
)


class ReplyState(TypedDict, total=False):
    notes_id: str
    draft_id: str
    outcome: str


class DoctorAssistant:
    spec = AgentSpec(
        kind=AgentKind.DOCTOR_ASSISTANT,
        acts_for_role=Role.DOCTOR,
        purposes=frozenset({Purpose.DOCTOR_BRIEF, Purpose.TREATMENT}),
        data_scope=frozenset(DataCategory)
        - {DataCategory.SENSITIVE_SEXUAL_HEALTH, DataCategory.SENSITIVE_MENTAL_HEALTH},
        tools=frozenset(
            {
                "record.read_for_treatment",
                "reports.list",
                "imaging.results",
                "safety.check_clashes",
                "cases.append",
            }
        ),
        may_message=frozenset(
            {AgentKind.PATIENT_COMPANION.value, Role.DOCTOR.value, Role.PATIENT.value}
        ),
        max_steps=8,
        max_wall_seconds=30,
        prompt_version="doctor-assistant-1",
    )

    async def handle(self, job: AgentJob, ctx: AgentContext) -> AgentResult:
        data = job.input
        trigger = data.get("trigger")
        if trigger in BRIEF_TRIGGERS:
            return await self._brief(ctx)
        if trigger == "request_fact" and data.get("fact_key"):
            return await self._request_fact(ctx, str(data["fact_key"]))
        if trigger == "notes" and data.get("notes_id"):
            return await self._reply(ctx, {"notes_id": str(data["notes_id"])})
        if trigger == "doctor_decision" and data.get("draft_id"):
            return await self._resume(ctx, MessageId(UUID(data["draft_id"])), str(data["decision"]))
        if "message_id" in data:  # the companion answered a question for the doctor
            answer = await ctx.message(MessageId(UUID(data["message_id"])))
            if answer is not None and answer.intent in (
                MessageIntent.MISSING_FACT_ANSWER,
                MessageIntent.PATIENT_QUESTION,
            ):
                return await self._brief(ctx)
        return AgentResult(status="done", reason="nothing_to_do")

    # ----- the brief ----------------------------------------------------------------------------

    async def _brief(self, ctx: AgentContext) -> AgentResult:
        try:
            state = await ctx.cases.get()
        except NotFound:
            return AgentResult(status="done", reason="case_not_visible")
        doctor = ctx.principal.acting_for
        if doctor is None:
            return AgentResult(status="done", reason="no_doctor")
        record = None
        if ctx.deps.record is not None:
            record = await ctx.deps.record.summary(
                ctx.principal, state.patient_id, Purpose.DOCTOR_BRIEF
            )
        adherence = await ctx.adherence(state.patient_id)
        clashes = []
        if record is not None and ctx.deps.safety is not None:
            clashes = ctx.deps.safety.check_clashes(
                list(record.medications), list(record.allergies), list(record.conditions)
            )
        now = ctx.clock.now()
        missing = missing_facts(state, record, now)
        brief = Brief(
            id=ResourceId(new_id()),
            case_id=state.id,
            patient_id=state.patient_id,
            version=await briefs.next_brief_version(ctx.uow, state.id),
            built_at=now,
            lines=tuple(
                ordered(build_lines(state, record, adherence, clashes, now, await _questions(ctx)))
            ),
            missing_facts=tuple(r.fact_key for r in missing),
            hidden_categories_count=record.hidden_categories_count if record else 0,
        )
        await briefs.save_brief(ctx.uow, brief, doctor)
        out: list[AgentMessageDraft] = [
            AgentMessageDraft(
                intent=MessageIntent.DOCTOR_BRIEF,
                recipient=Role.DOCTOR.value,
                body={"brief_id": str(brief.id)},
                consent_scope=BRIEF_SCOPE,
                dedupe_hint=f"brief:{brief.id}",
            )
        ]
        if state.status is not CaseStatus.EMERGENCY:  # no routine questions in an emergency
            for rule in missing:
                out.append(_ask_fact(state, rule.fact_key, rule.question_key, rule.why_key))
        return AgentResult(
            status="done",
            case_events=(
                CaseEventDraft(
                    kind=K.BRIEF_UPDATED,
                    data={"brief_id": str(brief.id)},
                    actor=self.spec.kind.value,
                ),
            ),
            messages=tuple(out),
            reason=f"brief_v{brief.version}",
        )

    async def _request_fact(self, ctx: AgentContext, fact_key: str) -> AgentResult:
        """The doctor asked for a fact from the case screen."""
        state = await ctx.cases.get()
        if state.status is CaseStatus.EMERGENCY:
            return AgentResult(status="done", reason="emergency")
        return AgentResult(
            status="waiting",
            messages=(_ask_fact(state, fact_key, f"q.{fact_key}", "why.doctor_asked"),),
        )

    # ----- the reply: draft, verify, approve, deliver (LangGraph) -----------------------------

    def _graph(self, ctx: AgentContext) -> Any:
        async def draft(state: ReplyState) -> ReplyState:
            case = await ctx.cases.get()
            text = await briefs.note_text(ctx.uow, UUID(state["notes_id"]))
            if text is None:
                return {"outcome": "notes_missing"}
            sent = await ctx.messenger.send(
                AgentMessageDraft(
                    intent=MessageIntent.REPLY_DRAFT,
                    recipient=Role.PATIENT.value,
                    body={
                        "text": text,  # the doctor's own words, unchanged
                        "language": case.language.value,
                        "based_on_notes_id": state["notes_id"],
                    },
                    consent_scope=frozenset({DataCategory.CONVERSATIONS}),
                    dedupe_hint=f"reply:{state['notes_id']}",
                )
            )
            if sent is None:
                return {"outcome": "already_drafted"}
            return {"draft_id": str(sent.message_id)}

        async def verify(state: ReplyState) -> ReplyState:
            draft_id = MessageId(UUID(state["draft_id"]))
            message = await messages.load(ctx.uow, draft_id)
            assert message is not None
            verdict = await ctx.deps.verifier.check(
                message,
                VerificationContext(
                    recipient_access=AccessDecision(
                        allowed=True, reason="patient_own_record", policy_version="runtime"
                    )
                ),
            )
            if verdict.verdict is not VerdictKind.ALLOW:  # fail closed (invariant 5)
                status = {
                    VerdictKind.REVISE: MessageStatus.RETURNED,
                    VerdictKind.ESCALATE: MessageStatus.ESCALATED,
                }.get(verdict.verdict, MessageStatus.BLOCKED)
                await messages.mark(ctx.uow, draft_id, status, (MessageStatus.DRAFTED,))
                return {"outcome": f"verifier_{verdict.verdict.value}"}
            await messages.mark(ctx.uow, draft_id, MessageStatus.HELD, (MessageStatus.DRAFTED,))
            return {}

        async def request_approval(state: ReplyState) -> ReplyState:
            case = await ctx.cases.get()
            doctor = ctx.principal.acting_for
            practitioner = await ctx.deps.directory.practitioner_of(doctor) if doctor else None
            if practitioner is None:
                return {"outcome": "no_practitioner"}
            await ctx.deps.cases.append(
                ctx.principal,
                case.id,
                [
                    CaseEventDraft(
                        kind=K.DOCTOR_APPROVAL_REQUESTED,
                        data={"draft_id": state["draft_id"], "practitioner_id": str(practitioner)},
                        actor=self.spec.kind.value,
                    )
                ],
                case.version,
            )
            return {}

        async def await_doctor(state: ReplyState) -> ReplyState:
            decision = interrupt({"draft_id": state["draft_id"]})  # resumed by the decision
            return {"outcome": str(decision)}

        async def deliver(state: ReplyState) -> ReplyState:
            outcome = await self._deliver(ctx, MessageId(UUID(state["draft_id"])), state["outcome"])
            return {"outcome": outcome}

        def stop_if_done(state: ReplyState) -> str:
            return END if state.get("outcome") else "next"

        graph = StateGraph(ReplyState)
        graph.add_node("draft", draft)
        graph.add_node("verify", verify)
        graph.add_node("request_approval", request_approval)
        graph.add_node("await_doctor", await_doctor)
        graph.add_node("deliver", deliver)
        graph.add_edge(START, "draft")
        graph.add_conditional_edges("draft", stop_if_done, {END: END, "next": "verify"})
        graph.add_conditional_edges("verify", stop_if_done, {END: END, "next": "request_approval"})
        graph.add_conditional_edges(
            "request_approval", stop_if_done, {END: END, "next": "await_doctor"}
        )
        graph.add_edge("await_doctor", "deliver")
        graph.add_edge("deliver", END)
        if ctx.checkpointer is None:
            raise RuntimeError("the reply flow needs a checkpointer (EVT-19)")
        return graph.compile(checkpointer=ctx.checkpointer)

    async def _reply(self, ctx: AgentContext, start: ReplyState) -> AgentResult:
        config = _thread(ctx, start["notes_id"])
        result = await self._graph(ctx).ainvoke(start, config)
        outcome = result.get("outcome") if isinstance(result, dict) else None
        return AgentResult(
            status="done" if outcome else "waiting", reason=outcome or "awaiting_doctor"
        )

    async def _resume(self, ctx: AgentContext, draft_id: MessageId, decision: str) -> AgentResult:
        draft = await messages.load(ctx.uow, draft_id)
        if draft is None or draft.intent is not MessageIntent.REPLY_DRAFT:
            return AgentResult(status="done", reason="draft_not_found")
        graph = self._graph(ctx)
        config = _thread(ctx, str(draft.body["based_on_notes_id"]))
        snapshot = await graph.aget_state(config)
        if snapshot.next:
            await graph.ainvoke(Command(resume=decision), config)
            return AgentResult(status="done", reason=f"reply_{decision}")
        # No paused run (for example the checkpoint was cleared): the case log still decides.
        outcome = await self._deliver(ctx, draft_id, decision)
        return AgentResult(status="done", reason=outcome)

    async def _deliver(self, ctx: AgentContext, draft_id: MessageId, decision: str) -> str:
        draft = await messages.load(ctx.uow, draft_id)
        if draft is None or draft.status is not MessageStatus.HELD:
            return "not_held"
        if decision not in ("approved", "edited"):
            await messages.mark(ctx.uow, draft_id, MessageStatus.RETURNED, (MessageStatus.HELD,))
            return "reply_rejected"
        case = await ctx.cases.get()  # EVT-21: fresh state after the pause
        text = await briefs.edited_text(ctx.uow, draft_id) or str(draft.body["text"])
        lang = case.language
        ask = Suggestion(
            kind="action",
            label=copy.chip("ask_doctor", lang),
            language=lang,
            source="step",
            action_kind="ask_doctor",
            action_id=f"ask:{case.id}",
        )
        reply = CompanionReply(kind="doctor_reply", text=text, language=lang, suggestions=(ask,))
        communication_id = await ctx.deps.cases.deliver_communication(
            case.id, "patient", reply.model_dump_json(), case.simulated
        )
        # The aggregate refuses a reply without a recorded approval (AGT-30); that error rolls
        # the whole job back, so the communication above is never kept without the approval.
        await ctx.deps.cases.append(
            ctx.principal,
            case.id,
            [
                CaseEventDraft(
                    kind=K.REPLY_DELIVERED,
                    data={"communication_id": str(communication_id), "draft_id": str(draft_id)},
                    actor=self.spec.kind.value,
                )
            ],
            case.version,
        )
        await messages.mark(ctx.uow, draft_id, MessageStatus.DELIVERED, (MessageStatus.HELD,))
        return "reply_delivered"


async def _questions(ctx: AgentContext) -> list[ResourceId]:
    if ctx.case_id is None:
        return []
    asked = await messages.of_intent(ctx.uow, ctx.case_id, MessageIntent.PATIENT_QUESTION)
    return [ResourceId(UUID(str(m.body["communication_id"]))) for m in asked]


def _thread(ctx: AgentContext, notes_id: str) -> dict[str, Any]:
    """One graph thread per reply (case, agent, notes), so two replies never share a pause."""
    base = ctx.graph_config()
    thread = f"{base['configurable']['thread_id']}:{notes_id}"
    return {"configurable": {"thread_id": thread}}


def _ask_fact(
    state: CaseState, fact_key: str, question_key: str, why_key: str
) -> AgentMessageDraft:
    return AgentMessageDraft(
        intent=MessageIntent.CONFIRM_MISSING_FACT,
        recipient=AgentKind.PATIENT_COMPANION.value,
        body={"fact_key": fact_key, "question_key": question_key, "why_key": why_key},
        consent_scope=frozenset({DataCategory.OBSERVATIONS}),
        dedupe_hint=f"fact:{state.id}:{fact_key}",  # each fact is asked once per case
    )
