"""verifier: deterministic checks, Jev policy, human reviews (VER-1 to VER-10)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field

from suwapath.contracts.agents import AgentMessage
from suwapath.contracts.common import Contract, Page
from suwapath.contracts.enums import DecidedBy, Urgency, VerdictKind
from suwapath.contracts.ids import MessageId, ResourceId
from suwapath.contracts.ports.consent import AccessDecision
from suwapath.contracts.principal import Principal


class VerificationContext(Contract):
    """What the verifier may see: never the author's prompt or reasoning (VER-1)."""

    recipient_access: AccessDecision | None
    case_urgency_floor: Urgency | None = None
    source_texts: dict[str, str] = Field(default_factory=dict)  # cited ids to source text
    known_identifiers: frozenset[str] = frozenset()  # for the canary check
    out_of_scope_values: frozenset[str] = frozenset()


class CheckResult(Contract):
    check: str
    passed: bool
    fixable: bool = True
    codes: tuple[str, ...] = ()


class VerifierCheck(Protocol):
    """Extension point (registry `verifier_checks`)."""

    name: str

    def run(self, message: AgentMessage, context: VerificationContext) -> CheckResult: ...


class Verdict(Contract):
    message_id: MessageId
    verdict: VerdictKind
    decided_by: DecidedBy
    checks: tuple[CheckResult, ...]
    model_probabilities: dict[str, float] = Field(default_factory=dict)
    model_checks: Literal["run", "not_run", "failed"]
    model_id: str | None = None
    cost_usd: float = 0.0
    latency_ms: int = 0
    at: datetime


class ReviewItem(Contract):
    id: ResourceId
    message_id: MessageId
    reviewer_role: Literal["doctor", "patient"]
    preview: str  # visible only to the reviewer
    reason_codes: tuple[str, ...]
    created_at: datetime


class Verifier(Protocol):
    async def check(self, message: AgentMessage, context: VerificationContext) -> Verdict: ...

    async def reviews(
        self, principal: Principal, cursor: str | None, limit: int
    ) -> Page[ReviewItem]: ...

    async def decide_review(
        self,
        principal: Principal,
        review_id: ResourceId,
        decision: Literal["send", "edit", "discard"],
        edited_text: str | None,
    ) -> Verdict: ...
