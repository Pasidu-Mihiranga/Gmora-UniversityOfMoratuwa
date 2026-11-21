"""Request-scoped trace id (W3C format). OpenTelemetry export is added with the gateway (W0.6)."""

from __future__ import annotations

from contextvars import ContextVar

from suwapath.platform.ids import new_trace_id

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def current_trace_id() -> str:
    value = _trace_id.get()
    if value is None:
        value = new_trace_id()
        _trace_id.set(value)
    return value


def set_trace_id(value: str) -> None:
    _trace_id.set(value)
