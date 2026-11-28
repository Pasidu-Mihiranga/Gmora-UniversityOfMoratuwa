"""`booking` module registration (ARC-3): the booking port, its timers and routes."""

from __future__ import annotations

from suwapath.modules.booking.api.router import build_router
from suwapath.modules.booking.application.service import SqlBookingService
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    service = SqlBookingService(container.uow_factory, container.ports.get)
    container.ports["booking"] = service
    container.subscribers.add(
        "cases.red_flag_raised", service.on_red_flag, name="booking.stop_on_red_flag"
    )
    container.subscribers.add("platform.timer_fired", service.on_timer, name="booking.hold_expiry")
    container.routers.append(build_router(service, lambda: container.ports.get("identity")))
