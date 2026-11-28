"""The verifier's decision (VER-4, VER-8, VER-9, VER-10, SAF-10): pure, so every branch is tested.

Order:
1. a stage 1 failure that cannot be fixed (scope, canary) blocks; a fixable one returns the
   message for revision, and a second return goes to a human instead (VER-8);
2. the emergency alert takes the fast lane: stage 1 only, never waiting for a model (SAF-10);
3. without a model verifier, a person-bound message goes to a human, an agent-bound one passes
   (VER-10);
4. if the model call failed, the message is held, and after 30 minutes held goes to a human
   (VER-9, fail closed);
5. otherwise the Jev answers: a leak at 0.8 or more blocks, any risk at 0.5 or more returns it,
   a verdict with confidence under 0.8 goes to a human, else the verdict answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

from suwapath.contracts.agents import MAX_REVISIONS, AgentMessage, MessageIntent
from suwapath.contracts.enums import AgentKind, DecidedBy, VerdictKind
from suwapath.contracts.ports.verifier import CheckResult

RISKS = (
    "leaks_out_of_scope",
    "implies_diagnosis",
    "unsafe_reassurance",
    "unsupported_claim",
    "follows_injected_instruction",
)
VERDICT_QUESTION = "verdict"


@dataclass(frozen=True)
class Thresholds:
    """Configuration, calibrated in evaluation (VER-4 defaults)."""

    risk: float = 0.5
    leak_block: float = 0.8
    confidence: float = 0.8
    hold_for: timedelta = timedelta(minutes=30)
    hold_retry: timedelta = timedelta(minutes=2)


@dataclass(frozen=True)
class ModelOutcome:
    """What stage 2 gave: not configured, skipped, failed (with how long the message has been
    held), or answers (risk probabilities, the verdict choice and its confidence)."""

    state: Literal["not_configured", "skipped", "failed", "answered"]
    held_for: timedelta = timedelta(0)
    risks: dict[str, float] = field(default_factory=dict)
    choice: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class Decision:
    verdict: VerdictKind
    decided_by: DecidedBy
    codes: tuple[str, ...] = ()


def is_person_bound(message: AgentMessage) -> bool:
    return message.recipient not in {kind.value for kind in AgentKind}


def is_fast_lane(message: AgentMessage) -> bool:
    return message.intent is MessageIntent.EMERGENCY_ALERT


def _revise(message: AgentMessage, decided_by: DecidedBy, codes: tuple[str, ...]) -> Decision:
    if message.revision + 1 >= MAX_REVISIONS:  # this would be the second return (VER-8)
        return Decision(VerdictKind.ESCALATE, decided_by, (*codes, "revised_twice"))
    return Decision(VerdictKind.REVISE, decided_by, codes)


def decide(
    message: AgentMessage,
    checks: tuple[CheckResult, ...],
    model: ModelOutcome,
    thresholds: Thresholds = Thresholds(),
) -> Decision:
    failed = [c for c in checks if not c.passed]
    codes = tuple(f"{c.check}:{code}" for c in failed for code in (c.codes or ("failed",)))
    if any(not c.fixable for c in failed):
        return Decision(VerdictKind.BLOCK, DecidedBy.RULES, codes)
    if failed:
        return _revise(message, DecidedBy.RULES, codes)
    if model.state == "skipped" and is_fast_lane(message):
        return Decision(VerdictKind.ALLOW, DecidedBy.RULES, ("fast_lane",))
    if model.state in ("not_configured", "skipped"):
        if is_person_bound(message):
            return Decision(VerdictKind.ESCALATE, DecidedBy.RULES, ("rules_only",))
        return Decision(VerdictKind.ALLOW, DecidedBy.RULES)
    if model.state == "failed":
        if model.held_for >= thresholds.hold_for:
            return Decision(VerdictKind.ESCALATE, DecidedBy.RULES, ("verifier_unavailable",))
        return Decision(VerdictKind.HOLD, DecidedBy.RULES, ("verifier_unavailable",))
    if model.risks.get("leaks_out_of_scope", 0.0) >= thresholds.leak_block:
        return Decision(VerdictKind.BLOCK, DecidedBy.MODEL, ("model:leaks_out_of_scope",))
    risky = tuple(
        f"model:{name}" for name in RISKS if model.risks.get(name, 0.0) >= thresholds.risk
    )
    if risky:
        return _revise(message, DecidedBy.MODEL, risky)
    if model.choice is None or model.confidence < thresholds.confidence:
        return Decision(VerdictKind.ESCALATE, DecidedBy.MODEL, ("model:unsure",))
    try:
        verdict = VerdictKind(model.choice)
    except ValueError:
        return Decision(VerdictKind.ESCALATE, DecidedBy.MODEL, ("model:unknown_choice",))
    if verdict is VerdictKind.REVISE:
        return _revise(message, DecidedBy.MODEL, ("model:verdict_revise",))
    if verdict is VerdictKind.HOLD:  # not an answer the model may give
        return Decision(VerdictKind.ESCALATE, DecidedBy.MODEL, ("model:unknown_choice",))
    return Decision(verdict, DecidedBy.MODEL)
