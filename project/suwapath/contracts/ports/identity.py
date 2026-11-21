"""identity: users, credentials, tokens (SEC-1 to SEC-6)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import Language, Role
from suwapath.contracts.ids import OrganizationId, PatientId, PractitionerId, UserId


class UserAccount(Contract):
    id: UserId
    role: Role
    display_name: str
    language: Language
    patient_id: PatientId | None = None  # role patient
    practitioner_id: PractitionerId | None = None  # role doctor
    organization_id: OrganizationId | None = None  # role hospital_admin
    linked_patient_ids: tuple[PatientId, ...] = ()  # role guardian


class IssuedTokens(Contract):
    access_token: str
    access_expires_at: datetime
    refresh_token: str  # set as an HttpOnly cookie by the API layer, never in a JSON body
    refresh_expires_at: datetime


class IdentityService(Protocol):
    async def authenticate(self, login: str, password: str, client_ip: str) -> IssuedTokens:
        """Raises AccessDenied on bad credentials or lockout (SEC-1)."""
        ...

    async def refresh(self, refresh_token: str) -> IssuedTokens:
        """Rotates the token; reuse of a revoked token revokes the family (SEC-3)."""
        ...

    async def logout(self, refresh_token: str) -> None: ...

    async def verify_access_token(self, token: str) -> UserAccount: ...

    async def get_user(self, user_id: UserId) -> UserAccount: ...
