"""Stub routes for endpoints whose module is not implemented yet (W0.8).

Each stub declares the real path, method and roles from spec 10 and answers 501 with the issue that
implements it, so the full API surface and OpenAPI exist from day one.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path

from suwapath.contracts.enums import Role
from suwapath.platform.http.auth import public, require

PATH_PARAM = re.compile(r"{([a-z_]+)}")
ALL_ROLES = tuple(Role)


@dataclass(frozen=True)
class StubRoute:
    method: str
    path: str  # under /api/v1
    roles: tuple[Role, ...]  # empty means public
    summary: str
    issue: str


def stub_router(tag: str, routes: list[StubRoute]) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=[tag])
    for route in routes:
        _add(router, route, tag)
    return router


def _add(router: APIRouter, route: StubRoute, tag: str) -> None:
    params = PATH_PARAM.findall(route.path)

    async def handler(**_: Any) -> None:
        raise HTTPException(status_code=501, detail=f"Not implemented yet ({route.issue})")

    handler.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter(p, inspect.Parameter.KEYWORD_ONLY, annotation=str, default=Path(...))
            for p in params
        ]
    )
    guard = Depends(require(*route.roles)) if route.roles else Depends(public())
    router.add_api_route(
        route.path,
        handler,
        methods=[route.method],
        summary=route.summary,
        dependencies=[guard],
        responses={501: {"description": f"Not implemented yet ({route.issue})"}},
        operation_id=f"{tag}_{route.method.lower()}_{_slug(route.path)}",
    )


def _slug(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")
