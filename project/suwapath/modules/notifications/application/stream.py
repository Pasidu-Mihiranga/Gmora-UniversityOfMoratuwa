"""`/me/stream` (API-20): one person's notifications, live.

With `Last-Event-ID` (a notification ID) it first replays what the person missed; without it the
stream starts now. A committed notification, or the end of quiet hours, wakes it; between wakes it
sends a heartbeat.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable

from suwapath.contracts.ids import UserId
from suwapath.modules.notifications.application.service import SqlNotificationService
from suwapath.platform.http.streams import HEARTBEAT, Subscription, sse


async def my_events(
    service: SqlNotificationService,
    user_id: UserId,
    subscription: Subscription,
    last_event_id: str | None,
    disconnected: Callable[[], Awaitable[bool]],
) -> AsyncGenerator[str]:
    try:
        position = await service.position_of(user_id, last_event_id)
        while True:
            items, position = await service.delivered_since(user_id, position)
            for item in items:
                yield sse("notification.created", item.model_dump(mode="json"), str(item.id))
            if await disconnected():
                return
            if not await subscription.wait():
                yield HEARTBEAT
    finally:
        subscription.close()
