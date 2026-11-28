"""`IdentityService` on Postgres (SEC-1 to SEC-6).

- Login: Argon2id; 5 failures per account or per IP in 10 minutes lock both for 10 minutes. The
  same answer for a wrong password, an unknown account and a lockout, so nothing is learned.
- Refresh: opaque, stored as a SHA-256 hash in a family. Every refresh rotates; presenting a
  revoked token revokes the whole family (reuse detection). Logout revokes the family, and an
  access token of a revoked family stops working at once.
- Stream tickets: hashed, 60 s, one use, bound to one user and one stream.
Attempts are stored as hashes of the login and the IP, never in clear.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, exists, func, select, update

from suwapath.contracts.enums import Language, Role
from suwapath.contracts.errors import AccessDenied, NotFound, ValidationFailed
from suwapath.contracts.ids import OrganizationId, PatientId, PractitionerId, UserId
from suwapath.contracts.ports.identity import (
    STREAM_TICKET_SECONDS,
    IssuedTokens,
    StreamTicket,
    UserAccount,
)
from suwapath.modules.identity.adapters.models import (
    LoginAttemptRow,
    RefreshTokenRow,
    StreamTicketRow,
    UserRow,
)
from suwapath.modules.identity.domain import credentials as cred
from suwapath.platform.ids import new_id
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

MAX_FAILURES = 5  # SEC-1
FAILURE_WINDOW = timedelta(minutes=10)
WRONG = "Email, phone or password is wrong, or there were too many tries. Try again later."


class SqlIdentityService:
    def __init__(self, uow_factory: UnitOfWorkFactory, jwt_secret: str) -> None:
        if len(jwt_secret) < 32:
            raise ValueError("JWT secret must be at least 32 characters")
        self._uow = uow_factory
        self._secret = jwt_secret

    # ----- sign in, refresh, sign out ---------------------------------------------------------

    async def authenticate(self, login: str, password: str, client_ip: str) -> IssuedTokens:
        login_key, ip_key = cred.login_hash(login), cred.token_hash(f"ip:{client_ip}")
        async with self._uow() as uow:
            now = uow.clock.now()
            if await self._locked(uow, login_key, ip_key, now):
                user = None
            else:
                user = (
                    await uow.session.execute(
                        select(UserRow).where(UserRow.login == cred.normalize_login(login))
                    )
                ).scalar()
            hashed = user.password_hash if user is not None and user.status == "active" else None
            ok = cred.verify_password(hashed, password)  # always runs: same time either way
            uow.session.add(LoginAttemptRow(login_hash=login_key, ip=ip_key, success=ok, at=now))
            if not ok or user is None:
                await uow.commit()  # the failure counts even though the login is refused
                raise AccessDenied(WRONG)
            tokens = await self._issue(uow, user, new_id(), now)
            await uow.commit()
        return tokens

    async def refresh(self, refresh_token: str) -> IssuedTokens:
        digest = cred.token_hash(refresh_token)
        async with self._uow() as uow:
            now = uow.clock.now()
            row = (
                await uow.session.execute(
                    select(RefreshTokenRow).where(RefreshTokenRow.token_hash == digest)
                )
            ).scalar()
            if row is None:
                raise AccessDenied("Sign in again")
            # Revoke this token; only one caller can win this update.
            won = (
                await uow.session.execute(
                    update(RefreshTokenRow)
                    .where(RefreshTokenRow.id == row.id, RefreshTokenRow.revoked_at.is_(None))
                    .values(revoked_at=now)
                    .returning(RefreshTokenRow.id)
                )
            ).scalar_one_or_none()
            if won is None:  # already used: a stolen token is being replayed (SEC-3)
                await self._revoke_family(uow, row.family_id, now)
                await uow.commit()
                raise AccessDenied("Sign in again")
            if row.expires_at <= now:
                await uow.commit()
                raise AccessDenied("Sign in again")
            user = await uow.session.get(UserRow, row.user_id)
            if user is None or user.status != "active":
                await self._revoke_family(uow, row.family_id, now)
                await uow.commit()
                raise AccessDenied("Sign in again")
            tokens = await self._issue(uow, user, row.family_id, now)
            await uow.commit()
        return tokens

    async def logout(self, refresh_token: str) -> None:
        async with self._uow() as uow:
            family = (
                await uow.session.execute(
                    select(RefreshTokenRow.family_id).where(
                        RefreshTokenRow.token_hash == cred.token_hash(refresh_token)
                    )
                )
            ).scalar()
            if family is not None:
                await self._revoke_family(uow, family, uow.clock.now())
                await uow.commit()

    async def verify_access_token(self, token: str) -> UserAccount:
        async with self._uow() as uow:
            now = uow.clock.now()
            try:
                claims = cred.read_access_token(self._secret, token, now)
            except cred.TokenInvalid:
                raise AccessDenied() from None
            family = UUID(claims["sid"])
            live = await uow.session.execute(
                select(
                    exists().where(
                        RefreshTokenRow.family_id == family,
                        RefreshTokenRow.revoked_at.is_(None),
                        RefreshTokenRow.expires_at > now,
                    )
                )
            )
            if not live.scalar():  # signed out or reuse detected: the session is over
                raise AccessDenied()
            user = await uow.session.get(UserRow, UUID(claims["sub"]))
            if user is None or user.status != "active" or user.role != claims["role"]:
                raise AccessDenied()
            return _account(user)

    async def get_user(self, user_id: UserId) -> UserAccount:
        async with self._uow.join() as uow:
            user = await uow.session.get(UserRow, user_id)
            if user is None:
                raise NotFound()
            return _account(user)

    # ----- stream tickets (SEC-5) ---------------------------------------------------------------

    async def issue_stream_ticket(self, user_id: UserId, stream: str) -> StreamTicket:
        ticket = cred.opaque_token()
        async with self._uow() as uow:
            expires = uow.clock.now() + timedelta(seconds=STREAM_TICKET_SECONDS)
            uow.session.add(
                StreamTicketRow(
                    ticket_hash=cred.token_hash(ticket),
                    user_id=user_id,
                    stream=stream[:80],
                    expires_at=expires,
                )
            )
            await uow.commit()
        return StreamTicket(ticket=ticket, stream=stream, expires_at=expires)

    async def redeem_stream_ticket(self, ticket: str, stream: str) -> UserAccount:
        async with self._uow() as uow:
            now = uow.clock.now()
            spent = (
                await uow.session.execute(
                    delete(StreamTicketRow)
                    .where(StreamTicketRow.ticket_hash == cred.token_hash(ticket))
                    .returning(
                        StreamTicketRow.user_id, StreamTicketRow.stream, StreamTicketRow.expires_at
                    )
                )
            ).first()
            await uow.session.execute(
                delete(StreamTicketRow).where(StreamTicketRow.expires_at <= now)
            )
            await uow.commit()  # one use: spent even when it does not match
        if spent is None or spent.stream != stream or spent.expires_at <= now:
            raise AccessDenied("Open the page again")
        return await self.get_user(UserId(spent.user_id))

    # ----- accounts (seed, admin, profile) ----------------------------------------------------

    async def create_user(
        self,
        *,
        login: str,
        password: str,
        role: Role,
        display_name: str,
        language: Language,
        patient_id: PatientId | None = None,
        practitioner_id: PractitionerId | None = None,
        organization_id: OrganizationId | None = None,
        user_id: UserId | None = None,
    ) -> UserAccount:
        """Idempotent by login: an existing account is returned unchanged (seed reruns)."""
        if len(password) < 8:
            raise ValidationFailed("password too short", fields=["password"])
        async with self._uow.join() as uow:
            existing = (
                await uow.session.execute(
                    select(UserRow).where(UserRow.login == cred.normalize_login(login))
                )
            ).scalar()
            if existing is not None:
                return _account(existing)
            row = UserRow(
                id=user_id or UserId(new_id()),
                role=role.value,
                login=cred.normalize_login(login),
                display_name=display_name[:100],
                language=language.value,
                password_hash=cred.hash_password(password),
                patient_id=patient_id,
                practitioner_id=practitioner_id,
                organization_id=organization_id,
            )
            uow.session.add(row)
            await uow.session.flush()
            account = _account(row)
        return account

    async def set_language(self, user_id: UserId, language: Language) -> UserAccount:
        async with self._uow() as uow:
            user = await uow.session.get(UserRow, user_id)
            if user is None:
                raise NotFound()
            user.language = language.value
            user.updated_at = uow.clock.now()
            account = _account(user)
            await uow.commit()
        return account

    # ----- internals ----------------------------------------------------------------------------

    async def _locked(self, uow: UnitOfWork, login_key: str, ip_key: str, now: datetime) -> bool:
        since = now - FAILURE_WINDOW
        for column, key in ((LoginAttemptRow.login_hash, login_key), (LoginAttemptRow.ip, ip_key)):
            failures = (
                await uow.session.execute(
                    select(func.count()).where(
                        column == key,
                        LoginAttemptRow.success.is_(False),
                        LoginAttemptRow.at > since,
                    )
                )
            ).scalar_one()
            if failures >= MAX_FAILURES:
                return True
        return False

    async def _issue(
        self, uow: UnitOfWork, user: UserRow, family: UUID, now: datetime
    ) -> IssuedTokens:
        refresh = cred.opaque_token()
        uow.session.add(
            RefreshTokenRow(
                id=new_id(),
                user_id=user.id,
                family_id=family,
                token_hash=cred.token_hash(refresh),
                expires_at=now + cred.REFRESH_TTL,
                created_at=now,
            )
        )
        await uow.session.flush()
        access, access_expires = cred.issue_access_token(
            self._secret, user_id=user.id, role=user.role, session_id=family, now=now
        )
        return IssuedTokens(
            access_token=access,
            access_expires_at=access_expires,
            refresh_token=refresh,
            refresh_expires_at=now + cred.REFRESH_TTL,
        )

    async def _revoke_family(self, uow: UnitOfWork, family: UUID, now: datetime) -> None:
        await uow.session.execute(
            update(RefreshTokenRow)
            .where(RefreshTokenRow.family_id == family, RefreshTokenRow.revoked_at.is_(None))
            .values(revoked_at=now)
        )


def _account(user: UserRow) -> UserAccount:
    return UserAccount(
        id=UserId(user.id),
        role=Role(user.role),
        display_name=user.display_name,
        language=Language(user.language),
        patient_id=PatientId(user.patient_id) if user.patient_id else None,
        practitioner_id=PractitionerId(user.practitioner_id) if user.practitioner_id else None,
        organization_id=OrganizationId(user.organization_id) if user.organization_id else None,
    )
