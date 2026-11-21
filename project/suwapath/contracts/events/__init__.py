"""Event envelope and the v1 catalog. Importing this package registers every payload."""

from suwapath.contracts.events import catalog as catalog  # noqa: F401  (registers payloads)
from suwapath.contracts.events.envelope import (
    EventEnvelope,
    EventPayload,
    payload_class,
    register_payload,
    registered_events,
)

__all__ = [
    "EventEnvelope",
    "EventPayload",
    "payload_class",
    "register_payload",
    "registered_events",
]
