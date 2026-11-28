"""Notifications on Postgres (M11): in-app items, quiet hours, channels from a timer after commit,
my list and read state, and `/me/stream` with replay and tickets."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import time, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest

from suwapath.contracts.enums import Language, PrincipalKind, Purpose, Role
from suwapath.contracts.errors import NotFound
from suwapath.contracts.ids import ResourceId, TraceId, UserId
from suwapath.contracts.ports.identity import UserAccount
from suwapath.contracts.ports.notifications import Notification, NotificationDraft
from suwapath.contracts.principal import Principal
from suwapath.contracts.testing.doubles_core import InMemoryIdentityService
from suwapath.modules.notifications.api.router import build_router
from suwapath.modules.notifications.application.service import (
    SqlNotificationService,
    channel_registry,
    stream_key,
)
from suwapath.modules.notifications.application.stream import my_events
from suwapath.modules.notifications.domain.quiet_hours import QuietHours
from suwapath.platform.clock import FixedClock
from suwapath.platform.config import Settings
from suwapath.platform.events.dispatcher import Dispatcher
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.http.app import create_app
from suwapath.platform.http.streams import StreamHub
from suwapath.platform.jobs.timers import fire_due_timers
from suwapath.platform.uow import UnitOfWorkFactory

pytestmark = pytest.mark.integration
TRACE = TraceId("6" * 32)
# The fixture clock is 2026-11-28 13:30 UTC = 19:00 in Colombo.


class RecordingSms:
    name = "sms"

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    async def send(self, notification: Notification) -> bool:
        self.sent.append(notification)
        return True


class Settings_:
    """The person's preferences, as C01 will bind them."""

    def __init__(self) -> None:
        self.quiet: dict[UserId, QuietHours] = {}
        self.sms: set[UserId] = set()

    async def quiet_hours(self, user_id: UserId) -> QuietHours | None:
        return self.quiet.get(user_id)

    async def channels(self, user_id: UserId, kind: str) -> frozenset[str]:
        return frozenset({"in_app", "sms"} if user_id in self.sms else {"in_app"})


class Inbox:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: FixedClock) -> None:
        self.uow_factory, self.clock = uow_factory, clock
        self.sms = RecordingSms()
        self.prefs = Settings_()
        channels = channel_registry()
        channels["sms"] = self.sms
        self.service = SqlNotificationService(uow_factory, lambda: channels, lambda: self.prefs)
        subscribers = SubscriberRegistry()
        subscribers.add("platform.timer_fired", self.service.on_timer, name="notifications")
        self.dispatcher = Dispatcher(uow_factory, subscribers)
        self.son, self.amma = UserId(uuid4()), UserId(uuid4())

    def me(self, user_id: UserId) -> Principal:
        return Principal(
            kind=PrincipalKind.USER,
            user_id=user_id,
            role=Role.GUARDIAN,
            acting_for=user_id,
            purpose=Purpose.SELF_ACCESS,
            trace_id=TRACE,
        )

    async def send(self, user_id: UserId, urgency: str = "normal", n: int = 1) -> Notification:
        return await self.service.notify(
            NotificationDraft(
                user_id=user_id,
                kind="guardian_alert",
                template_key=f"guardian_alert.v{n}",
                params={"level": urgency},
                urgency=urgency,
            )
        )

    async def pump(self) -> None:
        for _ in range(10):
            if not (await fire_due_timers(self.uow_factory) + await self.dispatcher.drain()):
                return
        raise AssertionError("did not settle")

    async def later(self, delta: timedelta) -> None:
        self.clock.advance(delta)
        await self.pump()

    async def listed(self, user_id: UserId) -> list[str]:
        page = await self.service.list(self.me(user_id), None, 50)
        return [n.template_key for n in page.items]


@pytest.fixture
def inbox(uow_factory: UnitOfWorkFactory, clock: FixedClock) -> Inbox:
    return Inbox(uow_factory, clock)


# ----- in-app items and read state ---------------------------------------------------------------


async def test_a_notification_is_mine_only_and_newest_first(inbox: Inbox) -> None:
    await inbox.send(inbox.son, n=1)
    await inbox.later(timedelta(minutes=1))
    await inbox.send(inbox.son, n=2)
    assert await inbox.listed(inbox.son) == ["guardian_alert.v2", "guardian_alert.v1"]
    assert await inbox.listed(inbox.amma) == []


async def test_the_cursor_pages_through_without_repeats(inbox: Inbox) -> None:
    for n in range(5):
        await inbox.send(inbox.son, n=n)
        inbox.clock.advance(timedelta(seconds=1))
    first = await inbox.service.list(inbox.me(inbox.son), None, 2)
    second = await inbox.service.list(inbox.me(inbox.son), first.next_cursor, 2)
    third = await inbox.service.list(inbox.me(inbox.son), second.next_cursor, 2)
    keys = [n.template_key for page in (first, second, third) for n in page.items]
    assert keys == [f"guardian_alert.v{n}" for n in (4, 3, 2, 1, 0)]
    assert third.next_cursor is None


async def test_marking_read_one_or_all_and_never_someone_elses(inbox: Inbox) -> None:
    one = await inbox.send(inbox.son, n=1)
    await inbox.send(inbox.son, n=2)
    theirs = await inbox.send(inbox.amma)
    with pytest.raises(NotFound):
        await inbox.service.mark_read(inbox.me(inbox.son), theirs.id)
    with pytest.raises(NotFound):
        await inbox.service.mark_read(inbox.me(inbox.son), ResourceId(uuid4()))
    await inbox.service.mark_read(inbox.me(inbox.son), one.id)
    page = await inbox.service.list(inbox.me(inbox.son), None, 10)
    assert [n.read_at is not None for n in page.items] == [False, True]
    await inbox.service.mark_read(inbox.me(inbox.son), None)
    page = await inbox.service.list(inbox.me(inbox.son), None, 10)
    assert all(n.read_at is not None for n in page.items)
    amma = await inbox.service.list(inbox.me(inbox.amma), None, 10)
    assert amma.items[0].read_at is None  # read-all is mine only


# ----- quiet hours and channels ------------------------------------------------------------------


async def test_quiet_hours_hold_a_normal_alert_until_the_window_ends(inbox: Inbox) -> None:
    inbox.prefs.quiet[inbox.son] = QuietHours(start=time(18, 0), end=time(23, 0))
    inbox.prefs.sms.add(inbox.son)
    await inbox.send(inbox.son)
    await inbox.pump()
    assert await inbox.listed(inbox.son) == [] and inbox.sms.sent == []
    await inbox.later(timedelta(hours=4, minutes=1))  # 23:01 in Colombo
    assert await inbox.listed(inbox.son) == ["guardian_alert.v1"]
    assert len(inbox.sms.sent) == 1


async def test_urgent_and_emergency_alerts_pass_quiet_hours(inbox: Inbox) -> None:
    inbox.prefs.quiet[inbox.son] = QuietHours(start=time(18, 0), end=time(23, 0))
    inbox.prefs.sms.add(inbox.son)
    await inbox.send(inbox.son, urgency="emergency")
    await inbox.pump()
    assert await inbox.listed(inbox.son) == ["guardian_alert.v1"]
    assert len(inbox.sms.sent) == 1


async def test_sms_only_when_chosen_and_only_after_commit(inbox: Inbox) -> None:
    await inbox.send(inbox.amma)
    await inbox.pump()
    assert inbox.sms.sent == []  # in-app is the default (spec 19)
    inbox.prefs.sms.add(inbox.son)
    async with inbox.uow_factory():  # the caller's transaction ends without a commit
        await inbox.send(inbox.son)
    await inbox.pump()
    assert inbox.sms.sent == [] and await inbox.listed(inbox.son) == []


# ----- /me/stream --------------------------------------------------------------------------------


@pytest.fixture
async def hub(database_url: str) -> AsyncIterator[StreamHub]:
    hub = StreamHub(database_url, heartbeat_seconds=0.3)
    yield hub
    await hub.close()


async def _never() -> bool:
    return False


async def _next(events: AsyncIterator[str]) -> tuple[str | None, str | None, Any]:
    chunk = await asyncio.wait_for(anext(events), timeout=5)
    if chunk.startswith(":"):
        return None, "heartbeat", None
    fields = dict(line.split(": ", 1) for line in chunk.strip().split("\n"))
    return fields.get("id"), fields["event"], json.loads(fields["data"])


async def test_the_stream_pushes_new_items_and_replays_after_last_event_id(
    inbox: Inbox, hub: StreamHub
) -> None:
    sub = await hub.subscribe(stream_key(inbox.son), inbox.son)
    events = my_events(inbox.service, inbox.son, sub, None, _never)
    assert (await _next(events))[1] == "heartbeat"  # starts now: nothing old
    first = await inbox.send(inbox.son, n=1)
    event_id, kind, data = await _next(events)
    assert (event_id, kind, data["template_key"]) == (
        str(first.id),
        "notification.created",
        "guardian_alert.v1",
    )
    await events.aclose()
    await inbox.send(inbox.son, n=2)  # sent while disconnected
    await inbox.send(inbox.amma)  # someone else's
    sub = await hub.subscribe(stream_key(inbox.son), inbox.son)
    events = my_events(inbox.service, inbox.son, sub, str(first.id), _never)
    assert (await _next(events))[2]["template_key"] == "guardian_alert.v2"
    assert (await _next(events))[1] == "heartbeat"
    await events.aclose()


async def test_a_held_item_reaches_the_stream_when_quiet_hours_end(
    inbox: Inbox, hub: StreamHub
) -> None:
    inbox.prefs.quiet[inbox.son] = QuietHours(start=time(18, 0), end=time(23, 0))
    sub = await hub.subscribe(stream_key(inbox.son), inbox.son)
    events = my_events(inbox.service, inbox.son, sub, None, _never)
    await inbox.send(inbox.son)
    assert (await _next(events))[1] == "heartbeat"  # held
    await inbox.later(timedelta(hours=4, minutes=1))
    kinds = [(await _next(events))[1] for _ in range(2)]
    assert "notification.created" in kinds
    await events.aclose()


async def test_the_stream_opens_only_with_a_ticket_for_me(inbox: Inbox, hub: StreamHub) -> None:
    identity = InMemoryIdentityService()
    son = UserAccount(id=inbox.son, role=Role.GUARDIAN, display_name="Son", language=Language.EN)
    identity.add_user(son, "son@example.test", "pw")
    app = create_app(
        Settings(_env_file=None, suwapath_env="test"),
        routers=[build_router(inbox.service)],
        ticket_redeemer=identity.redeem_stream_ticket,
        stream_hub=hub,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        assert (await client.get("/api/v1/me/stream")).status_code == 401
        case_ticket = await identity.issue_stream_ticket(son.id, f"case:{uuid4()}")
        response = await client.get(f"/api/v1/me/stream?ticket={case_ticket.ticket}")
        assert response.status_code == 401  # a ticket for another stream
