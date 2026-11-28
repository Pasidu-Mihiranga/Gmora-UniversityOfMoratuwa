"""Composition root (ARC-3, ARC-10): the only place that imports module packages and wires them."""

from __future__ import annotations

import importlib
import os
import signal
import socket
from contextlib import suppress
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from fastapi import APIRouter, FastAPI

from suwapath.contracts.ports.identity import UserAccount
from suwapath.platform.clock import Clock, SystemClock
from suwapath.platform.clock_store import SharedClock
from suwapath.platform.config import Settings, get_settings
from suwapath.platform.db import Database
from suwapath.platform.events.subscribers import SubscriberRegistry
from suwapath.platform.http.app import create_app
from suwapath.platform.http.auth import TokenVerifier
from suwapath.platform.http.streams import StreamHub
from suwapath.platform.tools import ToolRegistry
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
    tools: ToolRegistry = field(default_factory=ToolRegistry)
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

    async def redeem_ticket(ticket: str, stream: str) -> UserAccount:
        identity = container.ports.get("identity")
        if identity is None:
            raise RuntimeError("identity is not bound")  # the guard answers 401
        account: UserAccount = await identity.redeem_stream_ticket(ticket, stream)
        return account

    app = create_app(
        container.settings,
        token_verifier=container.token_verifier,
        ticket_redeemer=redeem_ticket,
        stream_hub=StreamHub(container.settings.database_url),
        routers=container.routers,
    )
    app.state.container = container
    return app


async def run_agent_worker(container: Container) -> None:
    """`python main.py agents`: one agent worker with the gateway and the LangGraph checkpointer."""
    import asyncio

    from suwapath.modules.agents.adapters.checkpoints import postgres_checkpointer
    from suwapath.modules.agents.application.runtime import AgentRunner
    from suwapath.modules.agents.application.worker import AgentWorker
    from suwapath.platform.gateway.factory import build_gateway

    gateway = build_gateway(
        container.settings, clock=container.clock, ops=container.ports.get("ops")
    )
    async with postgres_checkpointer(container.settings.database_url) as checkpointer:
        runner = AgentRunner(
            container.uow_factory,
            container.ports["agent_registry"],
            container.ports["agent_dependencies"],
            tools=container.tools,
            gateway=gateway,
            checkpointer=checkpointer,
        )
        worker = AgentWorker(runner, f"{socket.gethostname()}:{os.getpid()}")
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, worker.stop)
        await worker.run()


async def seed_demo_accounts(container: Container, seed: int = 42) -> list[str]:
    """`python main.py seed accounts`: the five demo sign-ins of spec 16, with DEMO_PASSWORD.
    Idempotent; refused in production. The full world loads with I01."""
    from suwapath.modules.identity.api.router import DEMO_LOGINS
    from suwapath.seed.generator import generate

    settings = container.settings
    password = settings.demo_password.get_secret_value()
    if settings.suwapath_env == "production" or not password:
        raise RuntimeError("demo accounts need DEMO_PASSWORD and are refused in production")
    identity = container.ports["identity"]
    users = {u.login: u for u in generate(seed).users if u.login in DEMO_LOGINS}
    for login in DEMO_LOGINS:
        user = users[login]
        await identity.create_user(
            login=user.login,
            password=password,
            role=user.role,
            display_name=user.display_name,
            language=user.language,
            patient_id=user.patient_id,
            practitioner_id=user.practitioner_id,
            organization_id=user.organization_id,
            user_id=user.id,
        )
    return list(DEMO_LOGINS)
