"""ASGI middleware: trace ids, security headers, no-store, body size limits (SG-17, SG-19, SG-3)."""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from suwapath.platform.ids import new_trace_id
from suwapath.platform.tracing import set_trace_id

JSON_BODY_LIMIT = 256 * 1024
UPLOAD_BODY_LIMIT = 10 * 1024 * 1024
UPLOAD_PATH_MARKERS = ("/reports", "/imaging", "/private/sessions/")
CACHEABLE_PREFIXES = ("/api/v1/health",)

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)


class SecurityMiddleware:
    """One pass over every HTTP request: trace id, size limit, security and cache headers."""

    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        trace_id = new_trace_id()
        set_trace_id(trace_id)
        path: str = scope.get("path", "")
        limit = (
            UPLOAD_BODY_LIMIT if any(m in path for m in UPLOAD_PATH_MARKERS) else JSON_BODY_LIMIT
        )

        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await _reject_too_large(send, trace_id)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge
            return message

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += _security_headers(path, trace_id, self.hsts)
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, limited_receive, send_with_headers)
        except _BodyTooLarge:
            await _reject_too_large(send, trace_id)


class _BodyTooLarge(Exception):
    pass


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _security_headers(path: str, trace_id: str, hsts: bool) -> list[tuple[bytes, bytes]]:
    headers = [
        (b"x-trace-id", trace_id.encode()),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-frame-options", b"DENY"),
        (b"permissions-policy", b"geolocation=(), microphone=(), camera=(self)"),
        (b"content-security-policy", CSP.encode()),
    ]
    if path.startswith("/api/") and not path.startswith(CACHEABLE_PREFIXES):
        headers.append((b"cache-control", b"no-store"))
    if hsts:
        headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
    return headers


async def _reject_too_large(send: Send, trace_id: str) -> None:
    body = json.dumps(
        {
            "type": "https://suwapath.dev/errors/payload_too_large",
            "title": "Payload too large",
            "status": 413,
            "code": "payload_too_large",
            "trace_id": trace_id,
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
                (b"x-trace-id", trace_id.encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
