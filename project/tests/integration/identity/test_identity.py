"""Critical tier: identity on Postgres (SEC-1 to SEC-6). Login and lockout, refresh rotation and
reuse detection, logout ending the session, stream tickets, and the cookie rules of the routes."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from suwapath.contracts.enums import Language, Role
from suwapath.contracts.errors import AccessDenied
from suwapath.modules.identity.adapters.models import LoginAttemptRow, RefreshTokenRow
from suwapath.modules.identity.api.router import COOKIE, build_router
from suwapath.modules.identity.application.service import SqlIdentityService
from suwapath.platform.clock import FixedClock
from suwapath.platform.config import Settings
from suwapath.platform.http.app import create_app
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
SECRET = "s" * 40
LOGIN, PASSWORD = "amma@example.test", "correct horse battery"


@pytest.fixture
async def identity(uow_factory: UnitOfWorkFactory) -> SqlIdentityService:
    service = SqlIdentityService(uow_factory, SECRET)
    await service.create_user(
        login=LOGIN,
        password=PASSWORD,
        role=Role.PATIENT,
        display_name="Kamala",
        language=Language.SI,
    )
    return service


# ----- login and lockout (SEC-1) ------------------------------------------------------------------


async def test_login_issues_a_session(identity: SqlIdentityService) -> None:
    tokens = await identity.authenticate(" AMMA@example.test ", PASSWORD, "10.0.0.1")
    account = await identity.verify_access_token(tokens.access_token)
    assert account.display_name == "Kamala" and account.role is Role.PATIENT


async def test_wrong_password_and_unknown_account_answer_the_same(
    identity: SqlIdentityService,
) -> None:
    with pytest.raises(AccessDenied) as wrong:
        await identity.authenticate(LOGIN, "nope", "10.0.0.1")
    with pytest.raises(AccessDenied) as unknown:
        await identity.authenticate("nobody@example.test", "nope", "10.0.0.1")
    assert wrong.value.message == unknown.value.message


async def test_five_failures_lock_the_account_for_ten_minutes(
    identity: SqlIdentityService, clock: FixedClock
) -> None:
    for _ in range(5):
        with pytest.raises(AccessDenied):
            await identity.authenticate(LOGIN, "wrong", "10.0.0.2")
    with pytest.raises(AccessDenied):  # even the right password is refused now
        await identity.authenticate(LOGIN, PASSWORD, "10.0.0.3")
    clock.advance(timedelta(minutes=10, seconds=1))
    assert await identity.authenticate(LOGIN, PASSWORD, "10.0.0.3")


async def test_five_failures_from_one_ip_lock_that_ip(identity: SqlIdentityService) -> None:
    for n in range(5):
        with pytest.raises(AccessDenied):
            await identity.authenticate(f"guess{n}@example.test", "x", "203.0.113.9")
    with pytest.raises(AccessDenied):
        await identity.authenticate(LOGIN, PASSWORD, "203.0.113.9")
    assert await identity.authenticate(LOGIN, PASSWORD, "198.51.100.1")  # another IP is fine


async def test_attempts_store_no_logins_or_ips(
    identity: SqlIdentityService, uow_factory: UnitOfWorkFactory
) -> None:
    with pytest.raises(AccessDenied):
        await identity.authenticate(LOGIN, "wrong", "10.0.0.7")
    async with uow_factory() as uow:
        row = (await uow.session.execute(select(LoginAttemptRow))).scalars().one()
        assert LOGIN not in row.login_hash and "10.0.0.7" not in row.ip


# ----- refresh rotation and reuse detection (SEC-3) ---------------------------------------------


async def test_refresh_rotates_and_the_old_token_stops_working(
    identity: SqlIdentityService,
) -> None:
    first = await identity.authenticate(LOGIN, PASSWORD, "10.0.0.1")
    second = await identity.refresh(first.refresh_token)
    assert second.refresh_token != first.refresh_token
    assert await identity.verify_access_token(second.access_token)


async def test_reusing_a_rotated_token_ends_the_whole_session(identity: SqlIdentityService) -> None:
    first = await identity.authenticate(LOGIN, PASSWORD, "10.0.0.1")
    second = await identity.refresh(first.refresh_token)
    with pytest.raises(AccessDenied):  # a thief replays the old token
        await identity.refresh(first.refresh_token)
    with pytest.raises(AccessDenied):  # and the real user's newer token is revoked too
        await identity.refresh(second.refresh_token)
    with pytest.raises(AccessDenied):  # as is the access token of that session
        await identity.verify_access_token(second.access_token)


async def test_tokens_are_stored_only_as_hashes(
    identity: SqlIdentityService, uow_factory: UnitOfWorkFactory
) -> None:
    tokens = await identity.authenticate(LOGIN, PASSWORD, "10.0.0.1")
    async with uow_factory() as uow:
        stored = (await uow.session.execute(select(RefreshTokenRow.token_hash))).scalars().all()
    assert tokens.refresh_token not in stored and len(stored[0]) == 64


async def test_an_expired_refresh_token_is_refused(
    identity: SqlIdentityService, clock: FixedClock
) -> None:
    tokens = await identity.authenticate(LOGIN, PASSWORD, "10.0.0.1")
    clock.advance(timedelta(days=7, seconds=1))
    with pytest.raises(AccessDenied):
        await identity.refresh(tokens.refresh_token)


async def test_logout_ends_the_session_at_once(identity: SqlIdentityService) -> None:
    tokens = await identity.authenticate(LOGIN, PASSWORD, "10.0.0.1")
    await identity.logout(tokens.refresh_token)
    with pytest.raises(AccessDenied):
        await identity.verify_access_token(tokens.access_token)
    with pytest.raises(AccessDenied):
        await identity.refresh(tokens.refresh_token)


async def test_access_tokens_expire_after_fifteen_minutes(
    identity: SqlIdentityService, clock: FixedClock
) -> None:
    tokens = await identity.authenticate(LOGIN, PASSWORD, "10.0.0.1")
    clock.advance(timedelta(minutes=15))
    with pytest.raises(AccessDenied):
        await identity.verify_access_token(tokens.access_token)


# ----- stream tickets (SEC-5) ---------------------------------------------------------------------


async def test_stream_tickets_are_one_use_bound_and_short_lived(
    identity: SqlIdentityService, clock: FixedClock
) -> None:
    tokens = await identity.authenticate(LOGIN, PASSWORD, "10.0.0.1")
    user = (await identity.verify_access_token(tokens.access_token)).id
    ticket = await identity.issue_stream_ticket(user, "me")
    assert (await identity.redeem_stream_ticket(ticket.ticket, "me")).id == user
    with pytest.raises(AccessDenied):
        await identity.redeem_stream_ticket(ticket.ticket, "me")
    other = await identity.issue_stream_ticket(user, "me")
    with pytest.raises(AccessDenied):
        await identity.redeem_stream_ticket(other.ticket, "case:x")
    late = await identity.issue_stream_ticket(user, "me")
    clock.advance(timedelta(seconds=61))
    with pytest.raises(AccessDenied):
        await identity.redeem_stream_ticket(late.ticket, "me")


# ----- routes: the cookie and CSRF (SEC-4, SG-6) ----------------------------------------------


async def test_routes_keep_the_refresh_token_in_a_strict_cookie(
    identity: SqlIdentityService,
) -> None:
    settings = Settings(_env_file=None, web_origins="http://localhost:5173")
    app = create_app(
        settings, token_verifier=identity.verify_access_token, routers=[build_router(identity)]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        signed_in = await client.post(
            "/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD}
        )
        assert signed_in.status_code == 200
        body = signed_in.json()
        assert "refresh_token" not in body and body["user"]["display_name"] == "Kamala"
        cookie = signed_in.headers["set-cookie"]
        for rule in ("HttpOnly", "Secure", "SameSite=strict", "Path=/api/v1/auth"):
            assert rule.lower() in cookie.lower()
        refused = await client.post("/api/v1/auth/refresh")  # no CSRF header
        assert refused.status_code == 403
        rotated = await client.post("/api/v1/auth/refresh", headers={"x-suwapath-request": "1"})
        assert rotated.status_code == 200
        bearer = {"Authorization": f"Bearer {rotated.json()['access_token']}"}
        assert (await client.get("/api/v1/me", headers=bearer)).json()["language"] == "si"
        ticket = await client.post("/api/v1/stream-tickets", json={"stream": "me"}, headers=bearer)
        assert ticket.status_code == 200 and ticket.json()["stream"] == "me"
        bad = await client.post(
            "/api/v1/stream-tickets", json={"stream": "case:../../x"}, headers=bearer
        )
        assert bad.status_code == 422
        assert (await client.post("/api/v1/auth/logout", headers=bearer)).status_code == 204
        assert (await client.get("/api/v1/me", headers=bearer)).status_code == 401
    assert COOKIE == "suwapath_refresh"
