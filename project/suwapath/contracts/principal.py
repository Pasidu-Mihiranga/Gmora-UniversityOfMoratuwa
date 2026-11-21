"""Who is acting, for whom and why (CON-6).

A Principal is built only by `platform.http.auth` (users), the agent runtime (agents) or the
platform itself (system tasks). Agents and models never construct or modify one, and tool arguments
never contain one.
"""

from __future__ import annotations

from pydantic import model_validator

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import AgentKind, PrincipalKind, Purpose, Role
from suwapath.contracts.ids import PatientId, TraceId, UserId


class Principal(Contract):
    kind: PrincipalKind
    user_id: UserId | None = None
    agent_kind: AgentKind | None = None
    role: Role | None = None
    acting_for: UserId | None = None
    patient_scope: PatientId | None = None
    purpose: Purpose
    trace_id: TraceId
    session_id: str | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> Principal:
        if self.kind is PrincipalKind.USER:
            if self.user_id is None or self.role is None:
                raise ValueError("user principal needs user_id and role")
            if self.agent_kind is not None:
                raise ValueError("user principal cannot carry agent_kind")
            if self.acting_for not in (None, self.user_id):
                raise ValueError("a user acts only for themselves")
        elif self.kind is PrincipalKind.AGENT:
            if self.agent_kind is None:
                raise ValueError("agent principal needs agent_kind")
            if self.agent_kind is not AgentKind.VERIFIER and self.acting_for is None:
                raise ValueError("person agents must act for a human (acting_for)")
            if self.user_id is not None:
                raise ValueError("agent principal must not carry user_id")
        elif self.user_id is not None or self.agent_kind is not None or self.role is not None:
            raise ValueError("system principal carries no user, agent or role")
        return self

    @property
    def effective_user(self) -> UserId | None:
        """The human whose rights bound this principal."""
        return self.user_id if self.kind is PrincipalKind.USER else self.acting_for
