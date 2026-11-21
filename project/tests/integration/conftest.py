"""Integration tests run against real Postgres (QA-3) in a separate `suwapath_test` database."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from suwapath.platform.clock import FixedClock
from suwapath.platform.db import Database
from suwapath.platform.uow import UnitOfWorkFactory

TEST_URL = os.environ.get(
    "SUWAPATH_TEST_DATABASE_URL",
    "postgresql+psycopg://suwapath:suwapath@localhost:5433/suwapath_test",
)
TABLES = (
    "platform_outbox",
    "platform_inbox",
    "platform_timers",
    "platform_flags",
    "platform_idempotency_keys",
    "agents_jobs",
)


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(scope="session")
def database_url() -> str:
    admin = _plain(TEST_URL).rsplit("/", 1)[0] + "/postgres"
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=3) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = 'suwapath_test'"
            ).fetchone()
            if not exists:
                conn.execute("CREATE DATABASE suwapath_test")
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable; start it with: docker compose up -d db")
    config = Config("alembic.ini")
    config.attributes["database_url"] = TEST_URL
    command.upgrade(config, "head")
    return TEST_URL


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[Database]:
    database = Database(database_url, pool_size=10)
    async with database.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY"))
        await conn.execute(text("UPDATE platform_clock SET offset_seconds = 0"))
    yield database
    await database.dispose()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 28, 13, 30, tzinfo=UTC))


@pytest.fixture
def uow_factory(db: Database, clock: FixedClock) -> UnitOfWorkFactory:
    return UnitOfWorkFactory(db, clock)
