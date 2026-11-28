"""What an agent may use during one job (spec 05 section 2).

Everything here is scoped by the runtime: the principal is built from the validated job, case reads
are limited to the job's case, model calls go through the gateway bound to the agent kind and
purpose, tools go through the `ToolInvoker`, and messages go through the outbox. Each model or tool
call takes one step from the job's budget (AGT-3).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ValidationError

from suwapath.contracts.agents import (
    MAX_HOPS,
    AgentJob,
    AgentMessage,
    AgentMessageDraft,
    AgentSpec,
    MessageIntent,
)
from suwapath.contracts.cases import CaseState
from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import AgentKind, DataCategory, Language
from suwapath.contracts.errors import AccessDenied, NotFound, ValidationFailed
from suwapath.contracts.ids import CaseId, MessageId, PatientId, PractitionerRoleId, UserId
from suwapath.contracts.ports.cases import CaseService
from suwapath.contracts.ports.consent import AccessPolicy, AccessRequest
from suwapath.contracts.ports.notifications import NotificationService
from suwapath.contracts.ports.verifier import Verifier
from suwapath.contracts.principal import Principal
from suwapath.modules.agents.application import messages
from suwapath.platform.clock import Clock
from suwapath.platform.gateway.types import ChatMessage, ChatRequest, ChatResponse
from suwapath.platform.uow import UnitOfWork

if TYPE_CHECKING:
    from suwapath.modules.agents.application.tools import ToolInvoker
    from suwapath.platform.gateway.gateway import ModelGateway


class JobLimit(Exception):  # noqa: N818  (a stop signal, not an error)
    """A per-job limit was reached (AGT-3, AGT-12). The job ends with handed_to_human."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Directory(Protocol):
    """Who is who. Bound at composition from identity, consent, booking and cases."""

    async def patient_user(self, patient_id: PatientId) -> UserId: ...
    async def guardians(self, patient_id: PatientId) -> list[UserId]: ...
    async def case_patient(self, case_id: CaseId) -> PatientId: ...
    async def language(self, patient_id: PatientId) -> Language: ...
    async def practitioner_user(
        self, practitioner_role_id: PractitionerRoleId
    ) -> UserId | None: ...
    async def doctor_case(self, patient_id: PatientId) -> tuple[CaseId, UserId] | None:
        """The patient's active case with a booked doctor, and that doctor's user."""
        ...


@dataclass
class Dependencies:
    cases: CaseService
    policy: AccessPolicy
    verifier: Verifier
    notifications: NotificationService
    directory: Directory
    missed_doses: Callable[[UnitOfWork, PatientId, timedelta], Awaitable[int]]
    latest_patient_text: Callable[[CaseId], Awaitable[UntrustedText | None]]


@dataclass
class StepCounter:
    limit: int
    used: int = 0

    def take(self) -> None:
        if self.used >= self.limit:
            raise JobLimit("max_steps")
        self.used += 1


class ScopedCaseReader:
    """Reads the job's case only, as the agent's principal. Remembers the version it saw, which
    becomes the expected version when the runtime appends the agent's case events (EVT-20)."""

    def __init__(self, cases: CaseService, principal: Principal) -> None:
        self._cases = cases
        self._principal = principal
        self.case_id: CaseId | None = None
        self.seen_version: int | None = None

    async def get(self) -> CaseState:
        if self.case_id is None:
            raise NotFound()
        state = await self._cases.get(self._principal, self.case_id)
        self.seen_version = state.version
        return state


class ScopedModelClient:
    """The gateway bound to (agent kind, purpose, case). Prompts and keys never leave it."""

    def __init__(
        self,
        gateway: ModelGateway | None,
        spec: AgentSpec,
        principal: Principal,
        cases: ScopedCaseReader,
        steps: StepCounter,
    ) -> None:
        self._gateway = gateway
        self._spec = spec
        self._principal = principal
        self._cases = cases
        self._steps = steps

    async def generate(
        self,
        step: str,
        messages: Sequence[ChatMessage],
        *,
        untrusted: Sequence[UntrustedText] = (),
        response_schema: type[BaseModel] | None = None,
        language: Language | None = None,
        fast: bool = False,
        max_output_tokens: int = 800,
    ) -> ChatResponse:
        if self._gateway is None:
            raise RuntimeError("no model gateway is bound to the agent runtime")
        self._steps.take()
        request = ChatRequest(
            agent_kind=self._spec.kind,
            purpose=self._principal.purpose,
            case_id=self._cases.case_id,
            step=step,
            messages=list(messages),
            untrusted=list(untrusted),
            response_schema=response_schema,
            language=language,
            fast=fast,
            max_output_tokens=max_output_tokens,
        )
        # Agent work is care work: a person's chat quota never blocks it (CUS-9a).
        return await self._gateway.generate(
            request, user_id=self._principal.acting_for, care_access=True
        )


class Messenger:
    """Sends messages through the outbox (AGT-10 to AGT-12). Stored once per dedupe key."""

    def __init__(
        self, uow: UnitOfWork, spec: AgentSpec, job: AgentJob, cases: ScopedCaseReader
    ) -> None:
        self._uow = uow
        self._spec = spec
        self._job = job
        self._cases = cases
        self.sent: list[AgentMessage] = []

    async def send(self, draft: AgentMessageDraft) -> AgentMessage | None:
        """The stored message, or None when this logical message was already sent."""
        if draft.recipient not in self._spec.may_message:
            raise AccessDenied("recipient not allowed for this agent", recipient=draft.recipient)
        case_id = self._cases.case_id
        if case_id is None:
            raise ValidationFailed("an agent message needs a case")
        if draft.in_reply_to is not None:
            parent = await messages.load(self._uow, draft.in_reply_to)
            if parent is None or parent.case_id != case_id:
                raise NotFound()
            if parent.hop + 1 >= MAX_HOPS:
                raise JobLimit("max_hops")
        key = draft.dedupe_hint or (
            f"{self._job.trigger_event_id or self._job.id}:{draft.intent.value}:{draft.recipient}"
        )
        try:
            message = await messages.send(
                self._uow,
                draft,
                sender=self._spec.kind,
                acting_for=self._job.acting_for,
                case_id=case_id,
                dedupe_key=key,
                trace_id=self._job.trace_id,
                now=self._uow.clock.now(),
            )
        except ValidationError as exc:
            fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
            raise ValidationFailed("invalid agent message", fields=fields) from None
        if message is not None:
            self.sent.append(message)
        return message


@dataclass
class AgentContext:
    """One job's view of the world. Built by the runtime, read only for the agent."""

    principal: Principal
    clock: Clock
    uow: UnitOfWork
    deps: Dependencies
    spec: AgentSpec
    job: AgentJob
    cases: ScopedCaseReader
    tools: ToolInvoker
    model: ScopedModelClient
    messenger: Messenger
    steps: StepCounter
    checkpointer: Any = None  # a LangGraph checkpoint saver; positions only (EVT-19)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def case_id(self) -> CaseId | None:
        return self.cases.case_id

    def graph_config(self) -> dict[str, Any]:
        """LangGraph run config: one thread per (case, agent) (EVT-19)."""
        if self.case_id is None:
            raise ValidationFailed("a graph run needs a case")
        return {"configurable": {"thread_id": f"{self.case_id}:{self.spec.kind.value}"}}

    async def open_case(self, patient_id: PatientId, reason: str) -> CaseState:
        """Open a case for this job (for example the guardian agent on missed doses)."""
        state = await self.deps.cases.open(
            self.principal,
            patient_id,
            UntrustedText(value=reason, source="system"),
            await self.deps.directory.language(patient_id),
        )
        self.cases.case_id = state.id
        self.cases.seen_version = state.version
        return state

    async def missed_doses(self, patient_id: PatientId, window: timedelta) -> int:
        decision = await self.deps.policy.decide(
            AccessRequest(
                principal=self.principal,
                action="read",
                patient_id=patient_id,
                category=DataCategory.ADHERENCE,
                resource_type="MedicationAdministration",
                purpose=self.principal.purpose,
            )
        )
        if not decision.allowed:
            return 0
        return await self.deps.missed_doses(self.uow, patient_id, window)

    async def message(self, message_id: MessageId) -> AgentMessage | None:
        """A message of this job's case, or None."""
        found = await messages.load(self.uow, message_id)
        if found is None or (self.case_id is not None and found.case_id != self.case_id):
            return None
        return found

    async def pending_request(self, intent: MessageIntent) -> AgentMessage | None:
        if self.case_id is None:
            return None
        return await messages.pending_request(self.uow, self.case_id, intent)

    async def has_answer(self, request_id: MessageId) -> bool:
        return await messages.has_answer(self.uow, request_id)

    async def patient_text(self) -> UntrustedText | None:
        """The patient's latest message in this case, as quoted data (invariant 7)."""
        if self.case_id is None or self.spec.kind is not AgentKind.PATIENT_COMPANION:
            return None
        return await self.deps.latest_patient_text(self.case_id)
