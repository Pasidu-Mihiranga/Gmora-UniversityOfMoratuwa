"""The case stream (API-20): what one viewer sees of one case, live.

On connect it sends `case.updated`, and with `Last-Event-ID` it first replays the timeline items
the viewer missed. Then it waits: a committed write wakes it, it re-reads what the viewer may see
(SEC-12) and sends the new items and the new state; between writes it sends a heartbeat. If the
viewer loses access (consent revoked), it sends `stream.closed` and ends.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from suwapath.contracts.cases import CaseState
from suwapath.contracts.errors import NotFound
from suwapath.contracts.ids import CaseId
from suwapath.contracts.principal import Principal
from suwapath.modules.cases.application.service import SqlCaseService
from suwapath.platform.http.streams import HEARTBEAT, Subscription, sse


def _updated(state: CaseState) -> dict[str, object]:
    return {
        "case_id": str(state.id),
        "version": state.version,
        "status": state.status.value,
        "urgency": state.urgency.value if state.urgency else None,
    }


def parse_last_event_id(value: str | None) -> int | None:
    if value is None or not value.strip().isdigit():
        return None
    return int(value.strip())


async def case_events(
    service: SqlCaseService,
    principal: Principal,
    case_id: CaseId,
    subscription: Subscription,
    last_event_id: int | None,
    disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    try:
        state, items, position = await service.timeline_since(principal, case_id, last_event_id)
        for seq, item in items:  # replay what was missed
            yield sse("case.timeline_appended", item.model_dump(mode="json"), str(seq))
        yield sse("case.updated", _updated(state))
        while not await disconnected():
            if not await subscription.wait():
                yield HEARTBEAT
                continue
            try:
                state, items, position = await service.timeline_since(principal, case_id, position)
            except NotFound:
                yield sse("stream.closed", {"reason": "access_ended"})
                return
            for seq, item in items:
                yield sse("case.timeline_appended", item.model_dump(mode="json"), str(seq))
            yield sse("case.updated", _updated(state))
    finally:
        subscription.close()
