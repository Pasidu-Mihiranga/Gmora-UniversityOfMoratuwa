"""notifications: channels, quiet hours, in-app items (NotificationChannel registry)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import Field

from suwapath.contracts.common import Contract, Page
from suwapath.contracts.ids import ResourceId, UserId
from suwapath.contracts.principal import Principal


class NotificationDraft(Contract):
    user_id: UserId
    kind: str  # e.g. "guardian_alert", "approval_required", "reply_delivered"
    template_key: str
    params: dict[str, Any] = Field(default_factory=dict)  # rendered per language; no PHI in SMS
    urgency: Literal["normal", "urgent", "emergency"] = (
        "normal"  # urgent and emergency skip quiet hours
    )
    link: str | None = None


class Notification(NotificationDraft):
    id: ResourceId
    created_at: datetime
    read_at: datetime | None = None


class NotificationChannel(Protocol):
    """Extension point (registry `notification_channels`)."""

    name: str

    async def send(self, notification: Notification) -> bool: ...


class NotificationService(Protocol):
    async def notify(self, draft: NotificationDraft) -> Notification:
        """Re-checks access at delivery time when content refers to patient data (SEC-13)."""
        ...

    async def list(
        self, principal: Principal, cursor: str | None, limit: int
    ) -> Page[Notification]: ...

    async def mark_read(self, principal: Principal, notification_id: ResourceId | None) -> None:
        """None marks all as read."""
        ...
