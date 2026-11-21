"""Subscriber registry (EVT-9). Handlers run in the delivery transaction and are idempotent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from suwapath.contracts.events import EventEnvelope, registered_events
from suwapath.platform.uow import UnitOfWork

Handler = Callable[[EventEnvelope, UnitOfWork], Awaitable[None]]


@dataclass(frozen=True)
class Subscriber:
    name: str  # "<module>.<purpose>", unique
    event_type: str
    handler: Handler
    versions: frozenset[int] = field(default_factory=lambda: frozenset({1}))


class SubscriberRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, Subscriber] = {}

    def add(
        self, event_type: str, handler: Handler, *, name: str, versions: set[int] | None = None
    ) -> None:
        if name in self._by_name:
            raise ValueError(f"subscriber {name} registered twice")
        accepted = frozenset(versions or {1})
        known = {(t, v) for (t, v) in registered_events()}
        unknown = [v for v in accepted if (event_type, v) not in known]
        if unknown:
            raise ValueError(f"{name}: unknown event {event_type} versions {unknown}")
        self._by_name[name] = Subscriber(name, event_type, handler, accepted)

    def for_event(self, event_type: str, version: int) -> list[Subscriber]:
        return [
            s
            for s in self._by_name.values()
            if s.event_type == event_type and version in s.versions
        ]

    def get(self, name: str) -> Subscriber:
        return self._by_name[name]

    def __len__(self) -> int:
        return len(self._by_name)
