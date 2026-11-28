"""`care` module registration (ARC-3): the care port, dose timers, alert rules and routes."""

from __future__ import annotations

from suwapath.modules.care.api.router import build_router, stubs
from suwapath.modules.care.application import doses
from suwapath.modules.care.application.service import SqlCareService
from suwapath.platform.registry import ModuleContext


def register(container: ModuleContext) -> None:
    ports = container.ports
    service = SqlCareService(
        container.uow_factory, lambda: ports.get("access_policy"), lambda: ports.get("record")
    )
    ports["care"] = service
    subscribers = container.subscribers
    subscribers.add("platform.timer_fired", doses.on_timer, name="care.dose_check")
    subscribers.add("platform.timer_fired", service.on_timer, name="care.dose_window")
    subscribers.add(
        "record.resource_written", service.on_resource_written, name="care.schedule_doses"
    )
    subscribers.add("care.dose_missed", service.on_dose_missed, name="care.alert_rules")
    container.routers.extend([build_router(service), stubs])
