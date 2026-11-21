from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4

import pytest

from suwapath.contracts.enums import DoseSlot, Urgency
from suwapath.contracts.events import (
    EventEnvelope,
    EventPayload,
    register_payload,
    registered_events,
)
from suwapath.contracts.events.catalog import DoseMissed, RedFlagRaised
from suwapath.contracts.ids import CaseId, EventId, PatientId, ResourceId, TraceId

# Field names that would suggest free text or identifiers inside an event (EVT-3).
PHI_LIKE_FIELDS = {
    "name",
    "text",
    "note",
    "message",
    "content",
    "quote",
    "value",
    "phone",
    "nic",
    "address",
    "email",
    "body",
    "answer",
    "reply",
}


def test_catalog_is_not_empty_and_types_are_namespaced() -> None:
    events = registered_events()
    assert len(events) >= 30
    for (event_type, version), cls in events.items():
        assert event_type.count(".") == 1, event_type
        assert version >= 1
        assert event_type.startswith(cls.producer + ".")


@pytest.mark.parametrize("cls", list(registered_events().values()), ids=lambda c: c.event_type)
def test_payloads_carry_no_free_text_fields(cls: type[EventPayload]) -> None:
    suspicious = PHI_LIKE_FIELDS & set(cls.model_fields)
    assert not suspicious, f"{cls.event_type} has PHI-like fields {suspicious}"


def test_envelope_round_trips_through_the_outbox_record() -> None:
    payload = DoseMissed(
        administration_id=ResourceId(uuid4()),
        statement_id=ResourceId(uuid4()),
        slot=DoseSlot.EVENING,
        scheduled_at=datetime(2026, 11, 27, 13, 30, tzinfo=UTC),
        patient_id=PatientId(uuid4()),
    )
    envelope = EventEnvelope.wrap(
        payload,
        event_id=EventId(uuid4()),
        occurred_at=datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
        aggregate_id=str(payload.administration_id),
        trace_id=TraceId("a" * 32),
        patient_id=payload.patient_id,
    )

    restored = EventEnvelope.from_record(envelope.to_record())

    assert restored == envelope
    assert isinstance(restored.payload, DoseMissed)
    assert restored.type == "care.dose_missed"


def test_red_flag_payload_keeps_urgency_typed() -> None:
    payload = RedFlagRaised(
        case_id=CaseId(uuid4()),
        patient_id=PatientId(uuid4()),
        rule_ids=("RF-NEURO-001",),
        urgency=Urgency.EMERGENCY,
    )
    record = EventEnvelope.wrap(
        payload,
        event_id=EventId(uuid4()),
        occurred_at=datetime.now(UTC),
        aggregate_id=str(payload.case_id),
        trace_id=TraceId("b" * 32),
    ).to_record()
    assert EventEnvelope.from_record(record).payload.urgency is Urgency.EMERGENCY  # type: ignore[attr-defined]


def test_duplicate_registration_is_rejected() -> None:
    with pytest.raises(ValueError, match="already registered"):

        @register_payload
        class Duplicate(EventPayload):  # noqa: F841
            event_type: ClassVar[str] = "care.dose_missed"
            producer: ClassVar[str] = "care"


def test_producer_must_match_type_prefix() -> None:
    with pytest.raises(ValueError, match="must start with its producer"):

        @register_payload
        class Mismatch(EventPayload):  # noqa: F841
            event_type: ClassVar[str] = "care.something_new"
            producer: ClassVar[str] = "booking"
