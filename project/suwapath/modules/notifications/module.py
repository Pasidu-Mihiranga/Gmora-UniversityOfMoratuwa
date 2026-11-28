"""`notifications` module registration (ARC-3): the notification port, its channel registry
(`notification_channels`, spec 15), the delivery timer and the routes."""

from __future__ import annotations

from suwapath.modules.notifications.api.router import build_router
from suwapath.modules.notifications.application.service import (
    NoPreferences,
    SqlNotificationService,
    channel_registry,
)
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    ports = container.ports
    ports["notification_channels"] = channel_registry()
    defaults = NoPreferences()
    service = SqlNotificationService(
        container.uow_factory,
        lambda: ports["notification_channels"],
        # C01 binds the person's settings (quiet hours, alert channel) under this name.
        lambda: ports.get("notification_preferences", defaults),
    )
    ports["notifications"] = service
    container.subscribers.add(
        "platform.timer_fired", service.on_timer, name="notifications.deliver"
    )
    container.routers.append(build_router(service))
