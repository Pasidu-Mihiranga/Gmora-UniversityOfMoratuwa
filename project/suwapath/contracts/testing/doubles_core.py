"""Test doubles for cross-cutting ports (QA-1). Deterministic, no I/O, no platform imports.

They implement the same rules as the specs where consumers depend on them (deny by default, append
only, quotas never block care access), and pass the shared contract suites in `suites.py`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from suwapath.contracts.agents import AgentJob, AgentMessage
from suwapath.contracts.common import Page
from suwapath.contracts.enums import (
    SENSITIVE_CATEGORIES,
    DataCategory,
    PrincipalKind,
    Purpose,
    Role,
)
from suwapath.contracts.errors import AccessDenied, NotFound, QuotaExceeded
from suwapath.contracts.ids import CaseId, JobId, PatientId, ResourceId, UserId
from suwapath.contracts.ports.audit import AuditEntry, AuditEntryDraft, AuditQuery
from suwapath.contracts.ports.consent import AccessDecision, AccessRequest
from suwapath.contracts.ports.identity import IssuedTokens, UserAccount
from suwapath.contracts.ports.notifications import Notification, NotificationDraft
from suwapath.contracts.ports.ops import ModelCallRecord, OpsOverview, UsageReservation
from suwapath.contracts.principal import Principal

Now = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ----- AccessPolicy -----------------------------------------------------------------------------


@dataclass
class InMemoryAccessPolicy:
    """SEC-10 rules over explicit test data. Deny by default."""

    patients_by_user: dict[UserId, PatientId] = field(default_factory=dict)
    guardian_grants: dict[tuple[UserId, PatientId], frozenset[DataCategory]] = field(
        default_factory=dict
    )
    treating: set[tuple[UserId, PatientId]] = field(default_factory=set)
    sensitive_for_doctor: set[tuple[UserId, PatientId]] = field(default_factory=set)
    agent_scopes: dict[str, frozenset[DataCategory]] = field(default_factory=dict)
    user_roles: dict[UserId, Role] = field(default_factory=dict)
    version: str = "test-1"

    async def decide(self, request: AccessRequest) -> AccessDecision:
        allowed, reason = self._decide(request)
        return AccessDecision(allowed=allowed, reason=reason, policy_version=self.version)

    def _decide(self, request: AccessRequest) -> tuple[bool, str]:
        principal = request.principal
        if principal.kind is PrincipalKind.SYSTEM:
            return False, "system_no_clinical_access"
        human = principal.effective_user
        if human is None:
            return False, "no_human"
        role = (
            principal.role if principal.kind is PrincipalKind.USER else self.user_roles.get(human)
        )
        if role is None:
            return False, "no_role"
        human_ok, reason = self._human_allows(human, role, request)
        if not human_ok:
            return False, reason
        if principal.kind is PrincipalKind.AGENT:
            scope = self.agent_scopes.get(
                principal.agent_kind.value if principal.agent_kind else "", frozenset()
            )
            if request.category not in scope:
                return False, "outside_agent_scope"
        return True, "allowed"

    def _human_allows(self, human: UserId, role: Role, request: AccessRequest) -> tuple[bool, str]:
        patient, category = request.patient_id, request.category
        if role in (Role.SYSTEM_ADMIN, Role.HOSPITAL_ADMIN):
            return False, "admin_no_clinical_access"
        if role is Role.PATIENT:
            return (self.patients_by_user.get(human) == patient), "not_own_record"
        if role is Role.GUARDIAN:
            if request.action == "write":
                return False, "guardian_read_only"
            granted = self.guardian_grants.get((human, patient), frozenset())
            return (category in granted), "guardian_grant_missing"
        if role is Role.DOCTOR:
            if (human, patient) not in self.treating:
                return False, "no_treating_relationship"
            if (
                category in SENSITIVE_CATEGORIES
                and (human, patient) not in self.sensitive_for_doctor
            ):
                return False, "sensitive_hidden"
            return True, "treating"
        return False, "unknown_role"


# ----- AuditLog ---------------------------------------------------------------------------------


@dataclass
class InMemoryAuditLog:
    now: Now = _utc_now
    entries: list[AuditEntry] = field(default_factory=list)

    async def record(self, entry: AuditEntryDraft) -> None:
        prev = self.entries[-1].hash if self.entries else "0" * 64
        at = self.now()
        body = json.dumps(entry.model_dump(mode="json"), sort_keys=True) + at.isoformat()
        digest = hashlib.sha256((prev + body).encode()).hexdigest()
        self.entries.append(
            AuditEntry(**entry.model_dump(), id=str(uuid4()), at=at, prev_hash=prev, hash=digest)
        )

    async def search(self, query: AuditQuery, cursor: str | None, limit: int) -> Page[AuditEntry]:
        rows = [
            e
            for e in reversed(self.entries)
            if (query.patient_id is None or e.patient_id == query.patient_id)
            and (query.actor_id is None or e.actor_id == query.actor_id)
            and (query.action is None or e.action == query.action)
            and (query.trace_id is None or e.trace_id == query.trace_id)
        ]
        start = int(cursor) if cursor else 0
        page = rows[start : start + limit]
        nxt = str(start + limit) if start + limit < len(rows) else None
        return Page(items=page, next_cursor=nxt)

    async def verify_chain(self) -> bool:
        prev = "0" * 64
        for entry in self.entries:
            draft = AuditEntryDraft(**entry.model_dump(exclude={"id", "at", "prev_hash", "hash"}))
            body = json.dumps(draft.model_dump(mode="json"), sort_keys=True) + entry.at.isoformat()
            if (
                entry.prev_hash != prev
                or entry.hash != hashlib.sha256((prev + body).encode()).hexdigest()
            ):
                return False
            prev = entry.hash
        return True


# ----- Identity ---------------------------------------------------------------------------------


@dataclass
class InMemoryIdentityService:
    now: Now = _utc_now
    users: dict[UserId, UserAccount] = field(default_factory=dict)
    passwords: dict[str, tuple[str, UserId]] = field(
        default_factory=dict
    )  # login -> (password, id)
    access: dict[str, UserId] = field(default_factory=dict)
    refresh_tokens: dict[str, UserId] = field(default_factory=dict)

    def add_user(self, account: UserAccount, login: str, password: str) -> None:
        self.users[account.id] = account
        self.passwords[login] = (password, account.id)

    def _issue(self, user_id: UserId) -> IssuedTokens:
        access, refresh = f"a-{uuid4()}", f"r-{uuid4()}"
        self.access[access] = user_id
        self.refresh_tokens[refresh] = user_id
        now = self.now()
        return IssuedTokens(
            access_token=access,
            access_expires_at=now,
            refresh_token=refresh,
            refresh_expires_at=now,
        )

    async def authenticate(self, login: str, password: str, client_ip: str) -> IssuedTokens:
        stored = self.passwords.get(login)
        if stored is None or stored[0] != password:
            raise AccessDenied("Email or password is wrong")
        return self._issue(stored[1])

    async def refresh(self, refresh_token: str) -> IssuedTokens:
        user_id = self.refresh_tokens.pop(refresh_token, None)
        if user_id is None:
            raise AccessDenied("Sign in again")
        return self._issue(user_id)

    async def logout(self, refresh_token: str) -> None:
        self.refresh_tokens.pop(refresh_token, None)

    async def verify_access_token(self, token: str) -> UserAccount:
        user_id = self.access.get(token)
        if user_id is None:
            raise AccessDenied("Sign in again")
        return self.users[user_id]

    async def get_user(self, user_id: UserId) -> UserAccount:
        if user_id not in self.users:
            raise NotFound()
        return self.users[user_id]


# ----- Ops (quotas) -----------------------------------------------------------------------------


@dataclass
class InMemoryOpsService:
    """CUS-9a and CUS-9b semantics: care access is never limited; quotas are per user."""

    turn_limit: int = 60
    cost_limit_usd: float = 0.30
    turns: dict[UserId, int] = field(default_factory=dict)
    cost: dict[UserId, float] = field(default_factory=dict)
    calls: list[ModelCallRecord] = field(default_factory=list)

    async def reserve(
        self,
        user_id: UserId | None,
        case_id: CaseId | None,
        estimated_cost_usd: float,
        care_access: bool,
    ) -> UsageReservation:
        if user_id is not None and not care_access:
            if self.turns.get(user_id, 0) >= self.turn_limit or (
                self.cost.get(user_id, 0.0) + estimated_cost_usd > self.cost_limit_usd
            ):
                raise QuotaExceeded("Daily limit reached")
            self.turns[user_id] = self.turns.get(user_id, 0) + 1
            self.cost[user_id] = self.cost.get(user_id, 0.0) + estimated_cost_usd
        return UsageReservation(
            id=str(uuid4()), user_id=user_id, case_id=case_id, estimated_cost_usd=estimated_cost_usd
        )

    async def reconcile(self, reservation: UsageReservation, record: ModelCallRecord) -> None:
        self.calls.append(record)
        if reservation.user_id is not None and reservation.user_id in self.cost:
            self.cost[reservation.user_id] += record.cost_usd - reservation.estimated_cost_usd

    async def overview(self) -> OpsOverview:
        return OpsOverview(
            provider_mode="offline",
            decision_provider="none",
            cost_today_usd=sum(c.cost_usd for c in self.calls),
            calls_today=len(self.calls),
            p95_latency_ms=None,
            outbox_lag_seconds=0.0,
            queued_agent_jobs=0,
            dead_letters=0,
        )


# ----- Notifications ----------------------------------------------------------------------------


@dataclass
class InMemoryNotificationService:
    now: Now = _utc_now
    items: list[Notification] = field(default_factory=list)

    async def notify(self, draft: NotificationDraft) -> Notification:
        item = Notification(**draft.model_dump(), id=ResourceId(uuid4()), created_at=self.now())
        self.items.append(item)
        return item

    async def list(
        self, principal: Principal, cursor: str | None, limit: int
    ) -> Page[Notification]:
        mine = [n for n in reversed(self.items) if n.user_id == principal.user_id]
        return Page(items=mine[:limit], next_cursor=None)

    async def mark_read(self, principal: Principal, notification_id: ResourceId | None) -> None:
        self.items = [
            n.model_copy(update={"read_at": self.now()})
            if n.user_id == principal.user_id
            and (notification_id is None or n.id == notification_id)
            else n
            for n in self.items
        ]


# ----- Agent runtime ----------------------------------------------------------------------------


@dataclass
class RecordingAgentRuntime:
    """Records jobs and messages so consumers can assert what they triggered."""

    jobs: list[AgentJob] = field(default_factory=list)
    messages: list[AgentMessage] = field(default_factory=list)

    async def enqueue(self, job: AgentJob) -> JobId:
        self.jobs.append(job)
        return job.id

    async def submit_message(self, message: AgentMessage) -> None:
        self.messages.append(message)


def user_principal(account: UserAccount, purpose: Purpose = Purpose.SELF_ACCESS) -> Principal:
    """Helper for tests: the Principal the auth dependency would build for this account."""
    from suwapath.contracts.ids import TraceId

    return Principal(
        kind=PrincipalKind.USER,
        user_id=account.id,
        role=account.role,
        acting_for=account.id,
        patient_scope=account.patient_id if account.role is Role.PATIENT else None,
        purpose=purpose,
        trace_id=TraceId("f" * 32),
    )
