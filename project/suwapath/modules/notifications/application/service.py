"""Notifications (M11): every notification is an in-app item; other channels (SMS) are extensions
from the `notification_channels` registry, chosen by the person's preferences.

A normal notification inside the person's quiet hours is held until the window ends; urgent and
emergency ones always pass (spec 19). The `/me/stream` push happens when the item is stored (or at
the end of quiet hours), and other channels run from a timer, so they only ever send what was
committed.

Access is checked by the caller at delivery time (SEC-13): the agent runtime re-evaluates the
recipients before it calls `notify`. A draft names a template and its params, never free text.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import and_, or_, select, tuple_, update

from suwapath.contracts.common import Page
from suwapath.contracts.errors import AccessDenied, NotFound
from suwapath.contracts.events import EventEnvelope
from suwapath.contracts.events.catalog import TimerFired
from suwapath.contracts.ids import ResourceId, UserId
from suwapath.contracts.ports.notifications import (
    Notification,
    NotificationChannel,
    NotificationDraft,
)
from suwapath.contracts.principal import Principal
from suwapath.modules.notifications.adapters.models import NotificationRow
from suwapath.modules.notifications.domain.quiet_hours import QuietHours, deliver_at
from suwapath.platform.http.streams import notify_stream
from suwapath.platform.ids import new_id
from suwapath.platform.uow import UnitOfWork, UnitOfWorkFactory

logger = logging.getLogger("suwapath.notifications")
SEND_TIMER = "notification_send"
IN_APP = "in_app"
MAX_PAGE = 100
ZERO = UUID(int=0)


def stream_key(user_id: UUID) -> str:
    """The hub key of one person's `/me/stream` (IDs only, no PHI)."""
    return f"me:{user_id}"


class Preferences(Protocol):
    """The person's own settings. C01 binds these from the customisation settings."""

    async def quiet_hours(self, user_id: UserId) -> QuietHours | None: ...
    async def channels(self, user_id: UserId, kind: str) -> frozenset[str]: ...


class NoPreferences:
    """Until C01: no quiet hours, in-app only (the spec 19 default)."""

    async def quiet_hours(self, user_id: UserId) -> QuietHours | None:
        return None

    async def channels(self, user_id: UserId, kind: str) -> frozenset[str]:
        return frozenset({IN_APP})


class InAppChannel:
    """The stored item is the in-app notification; nothing more to send."""

    name = IN_APP

    async def send(self, notification: Notification) -> bool:
        return True


class SmsChannel:
    """No-op sender (spec 15). A real provider sends the template in the person's language and
    never PHI; this one only logs that it would have sent, without the content."""

    name = "sms"

    async def send(self, notification: Notification) -> bool:
        logger.info(
            "sms not sent (no-op channel)",
            extra={
                "notification_kind": notification.kind,
                "template_key": notification.template_key,
            },
        )
        return True


def _notification(row: NotificationRow) -> Notification:
    return Notification(
        id=ResourceId(row.id),
        user_id=UserId(row.user_id),
        kind=row.kind,
        template_key=row.template_key,
        params=row.params,
        urgency=row.urgency,
        link=row.link,
        created_at=row.created_at,
        read_at=row.read_at,
    )


def _own(principal: Principal) -> UserId:
    if principal.kind != "user" or principal.user_id is None:
        raise AccessDenied()
    return UserId(principal.user_id)


class SqlNotificationService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        channels: Callable[[], Mapping[str, NotificationChannel]],
        preferences: Callable[[], Preferences],
    ) -> None:
        self._uow = uow_factory
        self._channels = channels
        self._preferences = preferences

    async def notify(self, draft: NotificationDraft) -> Notification:
        quiet = await self._preferences().quiet_hours(draft.user_id)
        async with self._uow.join() as uow:
            now = uow.clock.now()
            row = NotificationRow(
                id=new_id(),
                user_id=draft.user_id,
                kind=draft.kind,
                template_key=draft.template_key,
                params=draft.params,
                urgency=draft.urgency,
                link=draft.link,
                created_at=now,
                deliver_at=deliver_at(now, draft.urgency, quiet),
            )
            uow.session.add(row)
            await uow.session.flush()
            if row.deliver_at <= now:
                await notify_stream(uow, stream_key(draft.user_id))
            await uow.schedule_timer(
                kind=SEND_TIMER,
                due_at=row.deliver_at,
                ref_type="notification",
                ref_id=str(row.id),
                dedupe_key=f"notification:{row.id}",
            )
            return _notification(row)

    async def on_timer(self, envelope: EventEnvelope, uow: UnitOfWork) -> None:
        """Delivery time: wake the person's stream (held items) and send on their channels."""
        payload = envelope.payload
        if not isinstance(payload, TimerFired) or payload.kind != SEND_TIMER:
            return
        row = await uow.session.get(NotificationRow, UUID(payload.ref_id))
        if row is None or row.read_at is not None:
            return
        await notify_stream(uow, stream_key(row.user_id))
        notification = _notification(row)
        wanted = await self._preferences().channels(UserId(row.user_id), row.kind)
        channels = self._channels()
        for name in sorted(wanted - {IN_APP}):
            channel = channels.get(name)
            if channel is not None:
                await channel.send(notification)

    async def list(
        self, principal: Principal, cursor: str | None, limit: int
    ) -> Page[Notification]:
        user = _own(principal)
        limit = max(1, min(limit, MAX_PAGE))
        async with self._uow.join() as uow:
            query = select(NotificationRow).where(
                NotificationRow.user_id == user, NotificationRow.deliver_at <= uow.clock.now()
            )
            if cursor is not None:
                after = await self._cursor_row(uow, user, cursor)
                query = query.where(
                    tuple_(NotificationRow.created_at, NotificationRow.id)
                    < tuple_(after.created_at, after.id)
                )
            rows = list(
                (
                    await uow.session.execute(
                        query.order_by(
                            NotificationRow.created_at.desc(), NotificationRow.id.desc()
                        ).limit(limit + 1)
                    )
                ).scalars()
            )
            more = len(rows) > limit
            items = [_notification(r) for r in rows[:limit]]
        return Page(items=items, next_cursor=str(items[-1].id) if more else None)

    async def mark_read(self, principal: Principal, notification_id: ResourceId | None) -> None:
        user = _own(principal)
        async with self._uow.join() as uow:
            where = [NotificationRow.user_id == user, NotificationRow.read_at.is_(None)]
            if notification_id is not None:
                row = await uow.session.get(NotificationRow, notification_id)
                if row is None or row.user_id != user:
                    raise NotFound()  # never reveal another person's item (CON-11)
                where.append(NotificationRow.id == notification_id)
            await uow.session.execute(
                update(NotificationRow).where(*where).values(read_at=uow.clock.now())
            )

    async def delivered_since(
        self, user_id: UserId, position: tuple[datetime, UUID] | None
    ) -> tuple[Sequence[Notification], tuple[datetime, UUID]]:
        """What the person's stream has not sent yet, oldest first, and the new position.
        Without a position the stream starts now (older items come from `GET /notifications`)."""
        async with self._uow.join() as uow:
            now = uow.clock.now()
            start = position or (now, ZERO)
            rows = list(
                (
                    await uow.session.execute(
                        select(NotificationRow)
                        .where(
                            NotificationRow.user_id == user_id,
                            NotificationRow.deliver_at <= now,
                            or_(
                                NotificationRow.deliver_at > start[0],
                                and_(
                                    NotificationRow.deliver_at == start[0],
                                    NotificationRow.id > start[1],
                                ),
                            ),
                        )
                        .order_by(NotificationRow.deliver_at, NotificationRow.id)
                        .limit(MAX_PAGE)
                    )
                ).scalars()
            )
            items = [_notification(r) for r in rows]
            last = (rows[-1].deliver_at, rows[-1].id) if rows else start
        return items, last

    async def position_of(
        self, user_id: UserId, event_id: str | None
    ) -> tuple[datetime, UUID] | None:
        """The stream position after `Last-Event-ID` (a notification of this person), if known."""
        try:
            wanted = UUID(event_id) if event_id else None
        except ValueError:
            return None
        if wanted is None:
            return None
        async with self._uow.join() as uow:
            row = await uow.session.get(NotificationRow, wanted)
            if row is None or row.user_id != user_id:
                return None
            return row.deliver_at, row.id

    async def _cursor_row(self, uow: UnitOfWork, user: UserId, cursor: str) -> NotificationRow:
        try:
            row = await uow.session.get(NotificationRow, UUID(cursor))
        except ValueError:
            row = None
        if row is None or row.user_id != user:
            raise NotFound()
        return row


def channel_registry(extra: Mapping[str, Any] | None = None) -> dict[str, NotificationChannel]:
    channels: dict[str, NotificationChannel] = {IN_APP: InAppChannel(), "sms": SmsChannel()}
    channels.update(extra or {})
    return channels
