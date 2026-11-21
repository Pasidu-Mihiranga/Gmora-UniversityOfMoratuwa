"""consent: access policy, grants, customisation settings (SEC-10 to SEC-15, CUS-1 to CUS-17)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import DataCategory, Language, Purpose, Role
from suwapath.contracts.fhir.resources import Consent
from suwapath.contracts.ids import PatientId, ResourceId, UserId
from suwapath.contracts.principal import Principal


class AccessRequest(Contract):
    principal: Principal
    action: Literal["read", "write"]
    patient_id: PatientId
    category: DataCategory
    resource_type: str
    purpose: Purpose


class AccessDecision(Contract):
    allowed: bool
    allowed_fields: frozenset[str] | None = None  # None means every field of the resource
    reason: str  # machine readable, e.g. "guardian_grant_missing"
    policy_version: str


class AccessPolicy(Protocol):
    async def decide(self, request: AccessRequest) -> AccessDecision:
        """Deny by default. No caching across requests (SEC-12)."""
        ...


class SettingValue(Contract):
    owner_id: UserId
    role: Role
    key: str
    value: Any
    version: int
    locked: bool = False  # true when an L0 to L2 layer fixes it (CUS-2)
    locked_reason_key: str | None = None
    updated_at: datetime


class RuleProposal(Contract):
    """A plain-language request parsed into catalogue settings (CUS-6)."""

    owner_id: UserId
    changes: dict[str, Any]
    explanation_key: str
    preview_key: str | None = None
    rejected: tuple[str, ...] = ()  # parts that are locked or unsupported, as reason keys
    language: Language


class Suggestion(Contract):
    id: ResourceId
    patient_id: PatientId
    guardian_id: UserId
    key: str
    current_value: Any
    proposed_value: Any
    status: Literal["pending", "accepted", "declined"]


class ConsentService(Protocol):
    async def grants_for_patient(
        self, principal: Principal, patient_id: PatientId
    ) -> list[Consent]: ...

    async def set_grant(
        self,
        principal: Principal,
        patient_id: PatientId,
        grantee_id: UserId,
        categories: frozenset[DataCategory],
        idempotency_key: str,
    ) -> Consent:
        """Only the patient changes grants (CUS-3)."""
        ...

    async def revoke_grant(
        self, principal: Principal, patient_id: PatientId, grantee_id: UserId
    ) -> None: ...

    async def settings(self, principal: Principal, owner_id: UserId) -> list[SettingValue]: ...

    async def change_settings(
        self, principal: Principal, owner_id: UserId, changes: dict[str, Any]
    ) -> list[SettingValue]:
        """Validates catalogue, bounds and layers; versions and audits (CUS-4, CUS-15)."""
        ...

    async def parse_rule(
        self, principal: Principal, text: str, language: Language
    ) -> RuleProposal: ...

    async def revert_setting(
        self, principal: Principal, owner_id: UserId, key: str, to_version: int
    ) -> SettingValue: ...

    async def suggest(
        self, principal: Principal, patient_id: PatientId, key: str, value: Any
    ) -> Suggestion: ...

    async def decide_suggestion(
        self, principal: Principal, suggestion_id: ResourceId, accept: bool
    ) -> Suggestion: ...
