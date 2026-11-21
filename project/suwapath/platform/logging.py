"""Structured JSON logs that cannot carry PHI (SEC-32).

Only allowlisted extra attributes are emitted. Log messages must be constant strings; values go in
allowlisted extras (IDs, codes, numbers). Anything else is dropped.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

ALLOWED_EXTRAS = frozenset(
    {
        "trace_id",
        "case_id",
        "patient_id",
        "user_id",
        "agent_kind",
        "job_id",
        "event_id",
        "event_type",
        "subscriber",
        "route",
        "method",
        "status",
        "duration_ms",
        "attempt",
        "error_code",
        "provider",
        "model",
        "cost_usd",
        "tokens_in",
        "tokens_out",
        "tier",
        "purpose",
        "count",
        "timer_kind",
    }
)
_STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None)))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "at": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.msg if isinstance(record.msg, str) else type(record.msg).__name__,
        }
        for key, value in vars(record).items():
            if key in _STANDARD or key not in ALLOWED_EXTRAS:
                continue
            payload[key] = (
                value if isinstance(value, int | float | bool) or value is None else str(value)
            )
        if record.exc_info and record.exc_info[0] is not None:
            # Exception type only: messages and tracebacks can contain user data.
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
