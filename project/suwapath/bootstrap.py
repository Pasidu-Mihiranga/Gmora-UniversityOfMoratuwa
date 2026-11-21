"""Composition root (ARC-3, ARC-10): the only place that imports module packages and wires them."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from fastapi import APIRouter, FastAPI

from suwapath.platform.clock import Clock, SystemClock
from suwapath.platform.clock_store import SharedClock
from suwapath.platform.config import Settings, get_settings
from suwapath.platform.db import Database
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.http.app import create_app
from suwapath.platform.http.auth import TokenVerifier
from suwapath.platform.uow import UnitOfWorkFactory

MODULES: tuple[str, ...] = (
    "identity",
    "consent",
    "record",
    "audit",
    "safety",
    "navigation",
    "booking",
    "care",
    "imaging",
    "reports",
    "knowledge",
    "private",
    "memory",
    "notifications",
    "cases",
    "verifier",
    "agents",
    "ops",
)

_MODULES_WITH_TABLES: tuple[str, ...] = (
    "identity",
    "consent",
    "record",
    "audit",
    "booking",
    "care",
    "imaging",
    "reports",
    "knowledge",
    "private",
    "memory",
    "notifications",
    "cases",
    "verifier",
    "agents",
    "ops",
)


def import_all_models() -> None:
    """Register every table on Base.metadata (Alembic and tests)."""
    importlib.import_module("suwapath.platform.models")
    for name in _MODULES_WITH_TABLES:
        importlib.import_module(f"suwapath.modules.{name}.adapters.models")


@dataclass
class Container:
    """Everything a process needs. Modules add their routers, subscribers and port bindings."""

    settings: Settings
    db: Database
    clock: Clock
    shared_clock: SharedClock | None
    uow_factory: UnitOfWorkFactory
    subscribers: SubscriberRegistry = field(default_factory=SubscriberRegistry)
    routers: list[APIRouter] = field(default_factory=list)
    ports: dict[str, Any] = field(default_factory=dict)  # port name -> implementation
    token_verifier: TokenVerifier | None = None


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or get_settings()
    db = Database(settings.database_url)
    shared = SharedClock() if settings.dev_tools_enabled else None
    clock: Clock = shared if shared is not None else SystemClock()
    container = Container(
        settings=settings,
        db=db,
        clock=clock,
        shared_clock=shared,
        uow_factory=UnitOfWorkFactory(db, clock),
    )
    for name in MODULES:
        module: ModuleType = importlib.import_module(f"suwapath.modules.{name}.module")
        module.register(container)
    return container


def create_api(settings: Settings | None = None) -> FastAPI:
    """uvicorn factory: `suwapath.bootstrap:create_api`."""
    container = build_container(settings)
    app = create_app(
        container.settings, token_verifier=container.token_verifier, routers=container.routers
    )
    app.state.container = container
    return app
