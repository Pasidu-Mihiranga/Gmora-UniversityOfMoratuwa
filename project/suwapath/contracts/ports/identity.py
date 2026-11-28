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


class StreamTicket(Contract):
    """SEC-5: SSE cannot send headers, so a stream opens with a one-use ticket."""

    ticket: str  # random, never logged (SEC-6)
    stream: str  # the stream it is bound to, e.g. "case:{case_id}" or "me"
    expires_at: datetime


STREAM_TICKET_SECONDS = 60


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

    async def issue_stream_ticket(self, user_id: UserId, stream: str) -> StreamTicket:
        """Valid for 60 s and one use, bound to the user and the stream (SEC-5)."""
        ...

    async def redeem_stream_ticket(self, ticket: str, stream: str) -> UserAccount:
        """The ticket's user. Raises AccessDenied when unknown, used, expired or for another
        stream; a ticket is spent by its first redemption, even a failed one."""
        ...
