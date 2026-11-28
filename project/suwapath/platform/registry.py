"""What a module sees at registration (ARC-3): the composition root satisfies this protocol, so
modules never import `suwapath.bootstrap`."""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter

from suwapath.platform.clock import Clock
from suwapath.platform.config import Settings
from suwapath.platform.db import Database
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.tools import ToolRegistry
from suwapath.platform.uow import UnitOfWorkFactory


class ModuleContext(Protocol):
    settings: Settings
    db: Database
    clock: Clock
    uow_factory: UnitOfWorkFactory
    subscribers: SubscriberRegistry
    tools: ToolRegistry
    routers: list[APIRouter]
    ports: dict[str, Any]
