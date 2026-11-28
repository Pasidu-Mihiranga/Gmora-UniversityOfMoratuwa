"""Auth, profile and stream ticket routes (spec 10, SEC-1 to SEC-6).

The refresh token travels only in an HttpOnly, Secure, SameSite=Strict cookie scoped to
/api/v1/auth (SEC-4); the access token is returned in the body and kept in memory by the client.
The refresh route is cookie-authenticated, so it also needs the CSRF header and a known Origin.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import Language, Role
from suwapath.contracts.errors import AccessDenied
from suwapath.contracts.ids import UserId
from suwapath.contracts.ports.identity import IssuedTokens, StreamTicket, UserAccount
from suwapath.contracts.principal import Principal
from suwapath.modules.identity.application.service import SqlIdentityService
from suwapath.platform.http.auth import public, require, require_csrf

COOKIE = "suwapath_refresh"
COOKIE_PATH = "/api/v1/auth"
Anyone = Annotated[Principal, Depends(require(*Role))]
STREAM = r"^(me|case:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"


class Login(Contract):
    login: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=1, max_length=200)


class Session(Contract):
    access_token: str
    access_expires_at: datetime
    user: UserAccount


class ProfileChange(Contract):
    language: Language


class TicketRequest(Contract):
    stream: str = Field(pattern=STREAM)


def build_router(service: SqlIdentityService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["identity"])

    async def _session(response: Response, tokens: IssuedTokens) -> Session:
        response.set_cookie(
            COOKIE,
            tokens.refresh_token,
            httponly=True,
            secure=True,
            samesite="strict",
            path=COOKIE_PATH,
            expires=tokens.refresh_expires_at,
        )
        user = await service.verify_access_token(tokens.access_token)
        return Session(
            access_token=tokens.access_token, access_expires_at=tokens.access_expires_at, user=user
        )

    @router.post(
        "/auth/login",
        summary="Sign in",
        operation_id="identity_post_auth_login",
        dependencies=[Depends(public())],
    )
    async def login(body: Login, request: Request, response: Response) -> Session:
        client = request.client.host if request.client else "unknown"
        tokens = await service.authenticate(body.login, body.password, client)
        return await _session(response, tokens)

    @router.post(
        "/auth/refresh",
        summary="Rotate the refresh token (cookie, CSRF checked)",
        operation_id="identity_post_auth_refresh",
        dependencies=[Depends(public()), Depends(require_csrf)],
    )
    async def refresh(request: Request, response: Response) -> Session:
        token = request.cookies.get(COOKIE)
        if not token:
            raise AccessDenied("Sign in again")
        return await _session(response, await service.refresh(token))

    @router.post(
        "/auth/logout",
        status_code=204,
        summary="Sign out",
        operation_id="identity_post_auth_logout",
    )
    async def logout(_: Anyone, request: Request, response: Response) -> None:
        token = request.cookies.get(COOKIE)
        if token:
            await service.logout(token)
        response.delete_cookie(
            COOKIE, path=COOKIE_PATH, secure=True, httponly=True, samesite="strict"
        )

    @router.get("/me", summary="My profile", operation_id="identity_get_me")
    async def me(principal: Anyone) -> UserAccount:
        assert principal.user_id is not None
        return await service.get_user(UserId(principal.user_id))

    @router.patch("/me", summary="Update my profile", operation_id="identity_patch_me")
    async def update_me(body: ProfileChange, principal: Anyone) -> UserAccount:
        assert principal.user_id is not None
        return await service.set_language(UserId(principal.user_id), body.language)

    @router.post(
        "/stream-tickets",
        summary="One-time SSE ticket",
        operation_id="identity_post_stream_tickets",
    )
    async def stream_ticket(body: TicketRequest, principal: Anyone) -> StreamTicket:
        assert principal.user_id is not None
        return await service.issue_stream_ticket(UserId(principal.user_id), body.stream)

    return router
