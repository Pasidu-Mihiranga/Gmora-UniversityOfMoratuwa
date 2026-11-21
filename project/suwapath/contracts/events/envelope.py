"""Event envelope and payload registry (docs/specs/03-events-and-processes.md sections 2 and 3).

Payloads carry IDs, enums and numbers, never PHI (EVT-3). Every payload class is registered by
`(type, version)` so the outbox can serialise it and subscribers can parse it back.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from suwapath.contracts.common import Contract
from suwapath.contracts.ids import CaseId, EventId, PatientId, TraceId


class EventPayload(Contract):
    """Base for every event payload. Subclasses set `event_type` and `event_version`."""

    event_type: ClassVar[str]
    event_version: ClassVar[int] = 1
    producer: ClassVar[str]


_REGISTRY: dict[tuple[str, int], type[EventPayload]] = {}


def register_payload[P: type[EventPayload]](cls: P) -> P:
    """Class decorator: add a payload to the catalog. Duplicate (type, version) is an error."""
    key = (cls.event_type, cls.event_version)
    if key in _REGISTRY and _REGISTRY[key] is not cls:
        raise ValueError(f"event {key} already registered by {_REGISTRY[key].__name__}")
    if cls.event_type.split(".", 1)[0] != cls.producer:
        raise ValueError(f"event type {cls.event_type} must start with its producer {cls.producer}")
    _REGISTRY[key] = cls
    return cls


def payload_class(event_type: str, version: int) -> type[EventPayload]:
    try:
        return _REGISTRY[(event_type, version)]
    except KeyError as exc:
        raise KeyError(f"unknown event {event_type} v{version}") from exc


def registered_events() -> dict[tuple[str, int], type[EventPayload]]:
    return dict(_REGISTRY)


class EventEnvelope(Contract):
    event_id: EventId
    type: str
    version: int
    occurred_at: datetime
    producer: str
    aggregate_id: str
    patient_id: PatientId | None = None
    correlation_id: CaseId | None = None
    causation_id: EventId | None = None
    trace_id: TraceId
    payload: EventPayload

    @classmethod
    def wrap(
        cls,
        payload: EventPayload,
        *,
        event_id: EventId,
        occurred_at: datetime,
        aggregate_id: str,
        trace_id: TraceId,
        patient_id: PatientId | None = None,
        correlation_id: CaseId | None = None,
        causation_id: EventId | None = None,
    ) -> EventEnvelope:
        return cls(
            event_id=event_id,
            type=payload.event_type,
            version=payload.event_version,
            occurred_at=occurred_at,
            producer=payload.producer,
            aggregate_id=aggregate_id,
            patient_id=patient_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            trace_id=trace_id,
            payload=payload,
        )

    def to_record(self) -> dict[str, Any]:
        """JSON-safe dict for the outbox row."""
        data = self.model_dump(mode="json", exclude={"payload"})
        data["payload"] = self.payload.model_dump(mode="json")
        return data

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> EventEnvelope:
        payload_type = payload_class(data["type"], data["version"])
        fields = {k: v for k, v in data.items() if k != "payload"}
        return cls(**fields, payload=payload_type.model_validate(data["payload"]))
