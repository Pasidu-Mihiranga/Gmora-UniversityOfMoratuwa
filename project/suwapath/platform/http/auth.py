"""Authentication dependency, role guards, CSRF check and the route role table (SG-1 to SG-6).

Every `/api/v1` route must declare either `require(...)` roles or `public()`; `check_route_roles`
runs at startup and refuses to start otherwise (SG-1).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from suwapath.contracts.enums import PrincipalKind, Purpose, Role
from suwapath.contracts.ids import TraceId
from suwapath.contracts.ports.identity import UserAccount
from suwapath.contracts.principal import Principal
from suwapath.platform.tracing import current_trace_id

TokenVerifier = Callable[[str], Awaitable[UserAccount]]
CSRF_HEADER = "x-suwapath-request"


class RoleGuard:
    """Dependency that authenticates the caller and checks the route's roles."""

    def __init__(
        self,
        roles: frozenset[Role],
        purpose: Purpose,
        purposes: Mapping[Role, Purpose] | None = None,
    ) -> None:
        if not roles:
            raise ValueError("a guarded route needs at least one role")
        self.roles = roles
        self.purpose = purpose
        self.purposes = dict(purposes or {})

    async def __call__(self, request: Request) -> Principal:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Sign in to continue.")
        verifier: TokenVerifier | None = getattr(request.app.state, "token_verifier", None)
        if verifier is None:
            raise HTTPException(status_code=503, detail="Authentication is not available.")
        try:
            account = await verifier(token)
        except Exception as exc:  # any verification failure is a plain 401
            raise HTTPException(status_code=401, detail="Sign in to continue.") from exc
        if account.role not in self.roles:
            raise HTTPException(status_code=403, detail="You do not have access to this.")
        principal = Principal(
            kind=PrincipalKind.USER,
            user_id=account.id,
            role=account.role,
            acting_for=account.id,
            patient_scope=account.patient_id if account.role is Role.PATIENT else None,
            purpose=self.purposes.get(account.role, self.purpose),
            trace_id=TraceId(current_trace_id()),
        )
        request.state.principal = principal
        return principal


TicketRedeemer = Callable[[str, str], Awaitable[UserAccount]]


class StreamTicketGuard(RoleGuard):
    """SEC-5: a stream authenticates with `?ticket=` (SSE cannot send headers). The ticket is
    redeemed once for the stream named by `stream_key(request)`; roles are checked as usual."""

    def __init__(
        self,
        roles: frozenset[Role],
        purpose: Purpose,
        purposes: Mapping[Role, Purpose] | None,
        stream_key: Callable[[Request], str],
    ) -> None:
        super().__init__(roles, purpose, purposes)
        self.stream_key = stream_key

    async def __call__(self, request: Request) -> Principal:
        ticket = request.query_params.get("ticket", "")
        redeemer: TicketRedeemer | None = getattr(request.app.state, "ticket_redeemer", None)
        if not ticket or redeemer is None:
            raise HTTPException(status_code=401, detail="Open the page again.")
        try:
            account = await redeemer(ticket, self.stream_key(request))
        except Exception as exc:  # unknown, used, expired or for another stream
            raise HTTPException(status_code=401, detail="Open the page again.") from exc
        if account.role not in self.roles:
            raise HTTPException(status_code=403, detail="You do not have access to this.")
        principal = Principal(
            kind=PrincipalKind.USER,
            user_id=account.id,
            role=account.role,
            acting_for=account.id,
            patient_scope=account.patient_id if account.role is Role.PATIENT else None,
            purpose=self.purposes.get(account.role, self.purpose),
            trace_id=TraceId(current_trace_id()),
        )
        request.state.principal = principal
        return principal


def require_stream(
    *roles: Role,
    stream_key: Callable[[Request], str],
    purpose: Purpose = Purpose.SELF_ACCESS,
    purposes: Mapping[Role, Purpose] | None = None,
) -> StreamTicketGuard:
    return StreamTicketGuard(frozenset(roles), purpose, purposes, stream_key)


class PublicMarker:
    """Dependency that marks a route as intentionally public."""

    async def __call__(self) -> None:
        return None


def require(
    *roles: Role,
    purpose: Purpose = Purpose.SELF_ACCESS,
    purposes: Mapping[Role, Purpose] | None = None,
) -> RoleGuard:
    """`purposes` sets a different purpose per role, for routes several roles share."""
    return RoleGuard(frozenset(roles), purpose, purposes)


def public() -> PublicMarker:
    return PublicMarker()


def require_csrf(request: Request) -> None:
    """SG-6: the cookie-authenticated refresh route needs a known Origin and the custom header."""
    allowed: list[str] = getattr(request.app.state, "allowed_origins", [])
    origin = request.headers.get("origin")
    if request.headers.get(CSRF_HEADER) != "1" or (origin is not None and origin not in allowed):
        raise HTTPException(status_code=403, detail="Request not allowed.")


def _walk(dependant: Dependant) -> Iterator[Any]:
    for dep in dependant.dependencies:
        yield dep.call
        yield from _walk(dep)


def _routes(routes: list[BaseRoute]) -> Iterator[tuple[str, set[str], Dependant]]:
    """Every API route with its full path, including routes inside included routers.

    FastAPI 0.165 keeps included routers as wrapper routes; `effective_route_contexts()` yields
    their routes with the final path and the merged dependencies.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route.path, set(route.methods or ()), route.dependant
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            for ctx in contexts():
                yield ctx.path, set(ctx.methods or ()), ctx.dependant


def route_role_table(app: FastAPI) -> dict[str, str]:
    """`"METHOD path" -> "public" | "role1,role2" | ""` for every /api/v1 route."""
    table: dict[str, str] = {}
    for path, methods, dependant in _routes(app.routes):
        if not path.startswith("/api/v1"):
            continue
        marks = [c for c in _walk(dependant) if isinstance(c, RoleGuard | PublicMarker)]
        for method in sorted(methods):
            key = f"{method} {path}"
            if any(isinstance(m, PublicMarker) for m in marks):
                table[key] = "public"
            elif marks:
                roles = sorted(
                    {r.value for m in marks if isinstance(m, RoleGuard) for r in m.roles}
                )
                table[key] = ",".join(roles)
            else:
                table[key] = ""
    return table


def check_route_roles(app: FastAPI) -> None:
    missing = [route for route, roles in route_role_table(app).items() if not roles]
    if missing:
        raise RuntimeError("routes without a role list (SG-1): " + "; ".join(missing))
