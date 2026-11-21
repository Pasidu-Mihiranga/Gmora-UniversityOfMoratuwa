"""RFC 9457 problem details for every error (API-4). Error bodies never echo user input."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from suwapath.contracts.errors import SuwaPathError
from suwapath.platform.tracing import current_trace_id

logger = logging.getLogger("suwapath.http")
PROBLEM_JSON = "application/problem+json"


def problem(status: int, code: str, title: str, detail: str | None = None) -> JSONResponse:
    body = {
        "type": f"https://suwapath.dev/errors/{code}",
        "title": title,
        "status": status,
        "code": code,
        "trace_id": current_trace_id(),
    }
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SuwaPathError)
    async def _domain(_: Request, exc: SuwaPathError) -> JSONResponse:
        logger.info("domain error", extra={"error_code": exc.code, "status": exc.http_status})
        return problem(
            exc.http_status, exc.code, exc.code.replace("_", " ").capitalize(), exc.message
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations and error types only; never the submitted values (they may be PHI).
        fields = sorted({".".join(str(p) for p in err.get("loc", ())[1:]) for err in exc.errors()})
        return problem(
            422, "validation_failed", "Validation failed", "Invalid fields: " + ", ".join(fields)
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: "unauthenticated",
            403: "access_denied",
            404: "not_found",
            405: "method_not_allowed",
            413: "payload_too_large",
            429: "rate_limited",
        }.get(exc.status_code, "http_error")
        detail = exc.detail if isinstance(exc.detail, str) else None
        return problem(exc.status_code, code, code.replace("_", " ").capitalize(), detail)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error", exc_info=exc)
        return problem(500, "internal_error", "Internal error")
