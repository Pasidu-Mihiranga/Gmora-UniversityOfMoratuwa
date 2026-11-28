"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from suwapath import __version__
from suwapath.platform.config import Settings, get_settings
from suwapath.platform.http.auth import TicketRedeemer, TokenVerifier, check_route_roles, public
from suwapath.platform.http.errors import install_error_handlers
from suwapath.platform.http.middleware import SecurityMiddleware
from suwapath.platform.http.ratelimit import RateLimiter
from suwapath.platform.http.streams import StreamHub

API_PREFIX = "/api/v1"


def _health_router() -> APIRouter:
    router = APIRouter(
        prefix=f"{API_PREFIX}/health", tags=["health"], dependencies=[Depends(public())]
    )

    @router.get("/live")
    async def live() -> dict[str, str]:
        """Liveness: the process is up and serving requests."""
        return {"status": "ok", "version": __version__}

    return router


def create_app(
    settings: Settings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    ticket_redeemer: TicketRedeemer | None = None,
    stream_hub: StreamHub | None = None,
    routers: Sequence[APIRouter] = (),
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if stream_hub is not None:
            await stream_hub.close()

    app = FastAPI(
        lifespan=lifespan,
        title="SuwaPath API",
        version=__version__,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url="/docs" if settings.dev_tools_enabled else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.token_verifier = token_verifier
    app.state.ticket_redeemer = ticket_redeemer
    app.state.stream_hub = stream_hub
    app.state.allowed_origins = settings.origins
    app.state.rate_limiter = RateLimiter()

    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_credentials=True,  # needed only by the refresh cookie route (SG-18)
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["authorization", "content-type", "idempotency-key", "x-suwapath-request"],
        expose_headers=["x-trace-id"],
    )
    app.add_middleware(SecurityMiddleware, hsts=settings.suwapath_env == "production")

    app.include_router(_health_router())
    for router in routers:
        app.include_router(router)

    check_route_roles(app)
    return app
