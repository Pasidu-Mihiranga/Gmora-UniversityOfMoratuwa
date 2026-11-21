"""FastAPI application factory."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from suwapath import __version__

API_PREFIX = "/api/v1"


def _health_router() -> APIRouter:
    router = APIRouter(prefix=f"{API_PREFIX}/health", tags=["health"])

    @router.get("/live")
    async def live() -> dict[str, str]:
        """Liveness: the process is up and serving requests."""
        return {"status": "ok", "version": __version__}

    return router


def create_app() -> FastAPI:
    app = FastAPI(
        title="SuwaPath API",
        version=__version__,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url="/docs",
    )
    app.include_router(_health_router())
    return app
