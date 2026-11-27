"""Important tier: the agent runtime on Postgres (AGT-1 to AGT-3, AGT-12, EVT-11 to EVT-13, EVT-19,
EVT-20). Happy paths and the main failure paths: conflicts, crashes, limits, refused recipients."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, TypedDict
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select, update

from suwapath.contracts.agents import (
    AgentJob,
    AgentMessageDraft,
    AgentResult,
    AgentSpec,
    MessageIntent,
)
from suwapath.contracts.cases import CaseEventDraft, CaseEventKind
from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import (
    AgentKind,
    CaseStatus,
    DataCategory,
    Language,
    PrincipalKind,
    Purpose,
    Role,
)
from suwapath.contracts.events import EventEnvelope
from suwapath.contracts.events.catalog import (
    AppointmentBooked,
    CaseOpened,
    RedFlagRaised,
)
from suwapath.contracts.ids import (
    AppointmentId,
    CaseId,
    EventId,
    JobId,
    PatientId,
    PractitionerRoleId,
    TraceId,
    UserId,
)
from suwapath.contracts.principal import Principal
from suwapath.contracts.testing.doubles_clinical import ScriptedVerifier
from suwapath.contracts.testing.doubles_core import (
    InMemoryAccessPolicy,
    InMemoryNotificationService,
)
from suwapath.modules.agents.adapters.checkpoints import postgres_checkpointer
from suwapath.modules.agents.adapters.models import AgentMessageRow
from suwapath.modules.agents.application import messages
from suwapath.modules.agents.application.context import AgentContext, Dependencies
from suwapath.modules.agents.application.runtime import (
    AgentRegistry,
    AgentRunner,
    Supervisor,
)
from suwapath.modules.cases.application.service import SqlCaseService
from suwapath.platform.clock import FixedClock
from suwapath.platform.jobs import queue
from suwapath.platform.models import AgentJobRow
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
TRACE = TraceId("0" * 31 + "9")
K = CaseEventKind
Behaviour = Callable[[AgentJob, AgentContext], Awaitable[AgentResult]]

COMPANION_SPEC = AgentSpec(
    kind=AgentKind.PATIENT_COMPANION,
    acts_for_role=Role.PATIENT,
    purposes=frozenset({Purpose.CARE_NAVIGATION}),
    data_scope=frozenset(DataCategory),
    tools=frozenset(),
    may_message=frozenset({AgentKind.GUARDIAN_AGENT.value}),
    max_steps=2,
    max_wall_seconds=1,
    prompt_version="test",
)


class ScriptedAgent:
    spec = COMPANION_SPEC

    def __init__(self, behaviour: Behaviour) -> None:
        self.behaviour = behaviour
        self.runs = 0

    async def handle(self, job: AgentJob, ctx: AgentContext) -> AgentResult:
        self.runs += 1
        return await self.behaviour(job, ctx)


@dataclass
class Directory:
    cases: SqlCaseService
    patient_user_id: UserId
    guardian_ids: list[UserId] = field(default_factory=list)

    async def patient_user(self, patient_id: PatientId) -> UserId:
        return self.patient_user_id

    async def guardians(self, patient_id: PatientId) -> list[UserId]:
        return self.guardian_ids

    async def case_patient(self, case_id: CaseId) -> PatientId:
        return await self.cases.patient_of(case_id)

    async def language(self, patient_id: PatientId) -> Language:
        return Language.EN

    async def practitioner_user(self, practitioner_role_id: PractitionerRoleId) -> UserId | None:
        return UserId(uuid4())

    async def doctor_case(self, patient_id: PatientId) -> None:
        return None


class World:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: FixedClock) -> None:
        self.uow_factory = uow_factory
        self.clock = clock
        self.patient = PatientId(uuid4())
        self.amma = UserId(uuid4())
        self.son = UserId(uuid4())
        self.policy = InMemoryAccessPolicy(
            patients_by_user={self.amma: self.patient},
            user_roles={self.amma: Role.PATIENT, self.son: Role.GUARDIAN},
            agent_scopes={AgentKind.PATIENT_COMPANION.value: frozenset(DataCategory)},
        )
        self.cases = SqlCaseService(uow_factory, lambda: self.policy)
        self.directory = Directory(self.cases, self.amma, [self.son])
        self.deps = Dependencies(
            cases=self.cases,
            policy=self.policy,
            verifier=ScriptedVerifier(),
            notifications=InMemoryNotificationService(),
            directory=self.directory,
            missed_doses=_no_doses,
            latest_patient_text=self.cases.latest_patient_text,
        )

    @property
    def amma_principal(self) -> Principal:
        return Principal(
            kind=PrincipalKind.USER,
            user_id=self.amma,
            role=Role.PATIENT,
            acting_for=self.amma,
            patient_scope=self.patient,
            purpose=Purpose.CARE_NAVIGATION,
            trace_id=TRACE,
        )

    async def open_case(self) -> CaseId:
        state = await self.cases.open(
            self.amma_principal,
            self.patient,
            UntrustedText(value="I feel dizzy", source="patient_message"),
            Language.EN,
        )
        return state.id

    def runner(self, agent: Any, checkpointer: Any = None) -> AgentRunner:
        return AgentRunner(
            self.uow_factory, AgentRegistry([agent]), self.deps, checkpointer=checkpointer
        )

    async def enqueue(self, case_id: CaseId) -> JobId:
        job = AgentJob(
            id=JobId(uuid4()),
            agent_kind=AgentKind.PATIENT_COMPANION,
            case_id=case_id,
            patient_id=self.patient,
            trigger_event_id=EventId(uuid4()),
            acting_for=self.amma,
            purpose=Purpose.CARE_NAVIGATION,
            trace_id=TRACE,
        )
        async with self.uow_factory() as uow:
            queue.enqueue(uow, job)
            await uow.commit()
        return job.id

    async def job(self, job_id: JobId) -> tuple[str, str | None, int]:
        async with self.uow_factory() as uow:
            row = (
                await uow.session.execute(
                    select(AgentJobRow.status, AgentJobRow.error_code, AgentJobRow.attempts).where(
                        AgentJobRow.id == job_id
                    )
                )
            ).one()
        return row.status, row.error_code, row.attempts

    async def status(self, case_id: CaseId) -> CaseStatus:
        return (await self.cases.get(self.amma_principal, case_id)).status

    async def message_count(self) -> int:
        async with self.uow_factory() as uow:
            query = select(func.count()).select_from(AgentMessageRow)
            return int((await uow.session.execute(query)).scalar_one())


async def _no_doses(*_: Any) -> int:
    return 0


@pytest.fixture
def world(uow_factory: UnitOfWorkFactory, clock: FixedClock) -> World:
    return World(uow_factory, clock)


def _intake() -> CaseEventDraft:
    return CaseEventDraft(kind=K.INTAKE_STARTED, actor="patient_companion")


# ----- registry ----------------------------------------------------------------------------------


def test_an_agent_kind_registers_once() -> None:
    registry = AgentRegistry([ScriptedAgent(_done)])
    with pytest.raises(ValueError, match="twice"):
        registry.add(ScriptedAgent(_done))
    assert registry.get(AgentKind.DOCTOR_ASSISTANT) is None


async def _done(job: AgentJob, ctx: AgentContext) -> AgentResult:
    return AgentResult(status="done")


# ----- case events with the version the agent saw (EVT-20) ---------------------------------------


async def test_case_events_are_appended_and_the_job_finishes(world: World) -> None:
    case_id = await world.open_case()

    async def intake(job: AgentJob, ctx: AgentContext) -> AgentResult:
        state = await ctx.cases.get()
        assert state.status is CaseStatus.OPEN
        assert ctx.principal.agent_kind is AgentKind.PATIENT_COMPANION
        assert ctx.principal.acting_for == world.amma
        return AgentResult(status="done", case_events=(_intake(),))

    job_id = await world.enqueue(case_id)
    assert await world.runner(ScriptedAgent(intake)).run_once("w1")
    assert await world.status(case_id) is CaseStatus.INTAKE
    assert (await world.job(job_id))[0] == "done"


async def test_a_conflict_runs_the_job_again_on_fresh_state(world: World) -> None:
    case_id = await world.open_case()

    async def racing(job: AgentJob, ctx: AgentContext) -> AgentResult:
        state = await ctx.cases.get()
        if state.status is CaseStatus.OPEN:  # first run: someone else moves the case meanwhile
            write = world.cases.append(world.amma_principal, case_id, [_intake()], state.version)
            await asyncio.create_task(write, context=contextvars.Context())
            fact = CaseEventDraft(kind=K.FACT_RECORDED, data={"key": "dizzy"}, actor="c")
            return AgentResult(status="done", case_events=(fact,))
        fact = CaseEventDraft(kind=K.FACT_RECORDED, data={"key": "dizzy"}, actor="c")
        return AgentResult(status="done", case_events=(fact,))

    agent = ScriptedAgent(racing)
    runner = world.runner(agent)
    job_id = await world.enqueue(case_id)
    await runner.run_once("w1")
    status, error, attempts = await world.job(job_id)
    assert (status, error, attempts) == ("queued", "VersionConflict", 1)
    world.clock.advance(timedelta(seconds=5))
    await runner.run_once("w1")
    assert (await world.job(job_id))[0] == "done"
    state = await world.cases.get(world.amma_principal, case_id)
    assert [f.key for f in state.facts] == ["dizzy"]
    assert agent.runs == 2


async def test_the_last_failed_attempt_hands_the_case_to_a_human(world: World) -> None:
    case_id = await world.open_case()

    async def broken(job: AgentJob, ctx: AgentContext) -> AgentResult:
        raise RuntimeError("model said something odd")

    runner = world.runner(ScriptedAgent(broken))
    job_id = await world.enqueue(case_id)
    for _ in range(3):
        await runner.run_once("w1")
        world.clock.advance(timedelta(seconds=30))
    assert (await world.job(job_id))[:2] == ("failed", "RuntimeError")
    assert await world.status(case_id) is CaseStatus.HANDED_TO_HUMAN


# ----- limits (AGT-3, AGT-12) --------------------------------------------------------------------


async def test_step_limit_hands_over_and_discards_the_jobs_work(world: World) -> None:
    case_id = await world.open_case()

    async def chatty(job: AgentJob, ctx: AgentContext) -> AgentResult:
        await ctx.messenger.send(_check_in_result())
        for _ in range(3):
            ctx.steps.take()
        return AgentResult(status="done")

    job_id = await world.enqueue(case_id)
    await world.runner(ScriptedAgent(chatty)).run_once("w1")
    assert (await world.job(job_id))[:2] == ("failed", "max_steps")
    assert await world.status(case_id) is CaseStatus.HANDED_TO_HUMAN
    assert await world.message_count() == 0


async def test_wall_time_limit_hands_over(world: World) -> None:
    case_id = await world.open_case()

    async def slow(job: AgentJob, ctx: AgentContext) -> AgentResult:
        await asyncio.sleep(3)
        return AgentResult(status="done")

    job_id = await world.enqueue(case_id)
    await world.runner(ScriptedAgent(slow)).run_once("w1")
    assert (await world.job(job_id))[:2] == ("failed", "max_wall_seconds")
    assert await world.status(case_id) is CaseStatus.HANDED_TO_HUMAN


async def test_a_conversation_stops_at_the_hop_limit(world: World) -> None:
    case_id = await world.open_case()
    async with world.uow_factory() as uow:
        request = await messages.send(
            uow,
            AgentMessageDraft(
                intent=MessageIntent.CHECK_IN_WITH_PATIENT,
                recipient=AgentKind.PATIENT_COMPANION.value,
                body={"reason": "missed_doses", "slots_missed": ["evening"], "missed_count": 2},
                consent_scope=frozenset({DataCategory.ADHERENCE}),
            ),
            sender=AgentKind.GUARDIAN_AGENT,
            acting_for=world.son,
            case_id=case_id,
            dedupe_key="hop-test",
            trace_id=TRACE,
            now=world.clock.now(),
        )
        assert request is not None
        await uow.session.execute(
            update(AgentMessageRow).where(AgentMessageRow.id == request.message_id).values(hop=5)
        )
        await uow.commit()

    async def reply(job: AgentJob, ctx: AgentContext) -> AgentResult:
        return AgentResult(
            status="done", messages=(_check_in_result(in_reply_to=request.message_id),)
        )

    job_id = await world.enqueue(case_id)
    await world.runner(ScriptedAgent(reply)).run_once("w1")
    assert (await world.job(job_id))[:2] == ("failed", "max_hops")


async def test_an_agent_cannot_message_outside_its_allowlist(world: World) -> None:
    case_id = await world.open_case()

    async def to_doctor(job: AgentJob, ctx: AgentContext) -> AgentResult:
        draft = _check_in_result().model_copy(update={"recipient": Role.DOCTOR.value})
        return AgentResult(status="done", messages=(draft,))

    job_id = await world.enqueue(case_id)
    await world.runner(ScriptedAgent(to_doctor)).run_once("w1")
    assert (await world.job(job_id))[:2] == ("queued", "AccessDenied")
    assert await world.message_count() == 0


def _check_in_result(in_reply_to: Any = None) -> AgentMessageDraft:
    return AgentMessageDraft(
        intent=MessageIntent.CHECK_IN_RESULT,
        recipient=AgentKind.GUARDIAN_AGENT.value,
        in_reply_to=in_reply_to,
        body={"answered": True, "patient_ok": "yes", "consented_summary_keys": []},
        consent_scope=frozenset({DataCategory.ADHERENCE}),
    )


# ----- supervisor (EVT-13) -----------------------------------------------------------------------


def _envelope(payload: Any, clock: FixedClock) -> EventEnvelope:
    return EventEnvelope.wrap(
        payload,
        event_id=EventId(uuid4()),
        occurred_at=clock.now(),
        aggregate_id="t",
        trace_id=TRACE,
    )


async def _jobs(world: World) -> list[tuple[str, str, str]]:
    async with world.uow_factory() as uow:
        rows = (
            await uow.session.execute(
                select(AgentJobRow.agent_kind, AgentJobRow.purpose, AgentJobRow.input)
            )
        ).all()
    return [(r.agent_kind, r.purpose, r.input.get("trigger", "")) for r in rows]


async def test_the_supervisor_maps_events_to_registered_agents(world: World) -> None:
    supervisor = Supervisor(world.deps, {AgentKind.PATIENT_COMPANION, AgentKind.GUARDIAN_AGENT})
    case_id = CaseId(uuid4())
    events = [
        (
            supervisor.on_case_opened,
            CaseOpened(case_id=case_id, patient_id=world.patient, opened_by="patient"),
        ),
        (
            supervisor.on_case_opened,
            CaseOpened(case_id=case_id, patient_id=world.patient, opened_by="guardian_agent"),
        ),
        (
            supervisor.on_red_flag,
            RedFlagRaised(
                case_id=case_id, patient_id=world.patient, rule_ids=("RF-1",), urgency="emergency"
            ),
        ),
        (
            supervisor.on_appointment_booked,  # no doctor assistant registered: no job
            AppointmentBooked(
                appointment_id=AppointmentId(uuid4()),
                practitioner_role_id=PractitionerRoleId(uuid4()),
                patient_id=world.patient,
                case_id=case_id,
            ),
        ),
    ]
    async with world.uow_factory() as uow:
        for handler, payload in events:
            await handler(_envelope(payload, world.clock), uow)
        await uow.commit()
    assert sorted(await _jobs(world)) == [
        ("guardian_agent", "emergency", "red_flag"),
        ("patient_companion", "care_navigation", "case_opened"),
    ]


# ----- LangGraph checkpoints: position per (case, agent) thread (EVT-19) ------------------------


class Count(TypedDict):
    runs: int


def _graph(checkpointer: Any) -> Any:
    graph = StateGraph(Count)
    graph.add_node("count", lambda state: {"runs": state.get("runs", 0) + 1})
    graph.add_edge(START, "count")
    graph.add_edge("count", END)
    return graph.compile(checkpointer=checkpointer)


async def test_graph_state_is_checkpointed_per_case_and_agent(
    world: World, database_url: str
) -> None:
    case_id = await world.open_case()
    seen: list[int] = []

    async def graph_agent(job: AgentJob, ctx: AgentContext) -> AgentResult:
        assert ctx.graph_config()["configurable"]["thread_id"] == f"{case_id}:patient_companion"
        previous = await _graph(ctx.checkpointer).aget_state(ctx.graph_config())
        start = previous.values.get("runs", 0) if previous.values else 0
        final = await _graph(ctx.checkpointer).ainvoke({"runs": start}, ctx.graph_config())
        seen.append(final["runs"])
        return AgentResult(status="done")

    async with postgres_checkpointer(database_url) as checkpointer:
        runner = world.runner(ScriptedAgent(graph_agent), checkpointer)
        for _ in range(2):
            await world.enqueue(case_id)
            await runner.run_once("w1")
    assert seen == [1, 2]
