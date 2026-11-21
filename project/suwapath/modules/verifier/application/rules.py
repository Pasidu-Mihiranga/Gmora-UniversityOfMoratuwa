"""Stage 1 of the verifier: deterministic checks (VER-2), with the no-model rule of VER-10.

The scope check refuses any message whose consent scope exceeds the recipient's current access.
The canary check refuses any message containing a value the recipient may not see. When no model
verifier is configured, person-bound messages that pass are escalated to a human (VER-10).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from suwapath.contracts.agents import AgentMessage
from suwapath.contracts.common import Page
from suwapath.contracts.enums import AgentKind, DecidedBy, VerdictKind
from suwapath.contracts.errors import NotFound
from suwapath.contracts.ids import ResourceId
from suwapath.contracts.ports.verifier import (
    CheckResult,
    ReviewItem,
    Verdict,
    VerificationContext,
    VerifierCheck,
)
from suwapath.contracts.principal import Principal


class ScopeCheck:
    name = "scope"

    def run(self, message: AgentMessage, context: VerificationContext) -> CheckResult:
        access = context.recipient_access
        passed = access is not None and access.allowed
        codes = () if passed else (access.reason if access else "no_access_decision",)
        return CheckResult(check=self.name, passed=passed, fixable=False, codes=codes)


class CanaryCheck:
    name = "canary"

    def run(self, message: AgentMessage, context: VerificationContext) -> CheckResult:
        content = json.dumps(message.body, ensure_ascii=False).casefold() + " ".join(
            q.value.casefold() for q in message.quoted
        )
        leaked = [v for v in context.out_of_scope_values if v and v.casefold() in content]
        return CheckResult(
            check=self.name,
            passed=not leaked,
            fixable=False,
            codes=("out_of_scope_value",) if leaked else (),
        )


class RulesVerifier:
    """Verifier port, stage 1 only. Stage 2 (Jev) is added in M16."""

    def __init__(
        self, now: Callable[[], datetime], *, model_checks_available: bool = False
    ) -> None:
        self.now = now
        self.checks: list[VerifierCheck] = [ScopeCheck(), CanaryCheck()]
        self.model_checks_available = model_checks_available

    async def check(self, message: AgentMessage, context: VerificationContext) -> Verdict:
        results = tuple(check.run(message, context) for check in self.checks)
        failed = [r for r in results if not r.passed]
        to_person = message.recipient not in {k.value for k in AgentKind}
        if failed:
            verdict = VerdictKind.REVISE if all(r.fixable for r in failed) else VerdictKind.BLOCK
        elif to_person and not self.model_checks_available:
            verdict = VerdictKind.ESCALATE  # VER-10: person-bound, no model verifier
        else:
            verdict = VerdictKind.ALLOW
        return Verdict(
            message_id=message.message_id,
            verdict=verdict,
            decided_by=DecidedBy.RULES,
            checks=results,
            model_checks="not_run",
            at=self.now(),
        )

    async def reviews(
        self, principal: Principal, cursor: str | None, limit: int
    ) -> Page[ReviewItem]:
        return Page(items=[])

    async def decide_review(
        self, principal: Principal, review_id: ResourceId, decision: str, edited_text: str | None
    ) -> Verdict:
        raise NotFound()
