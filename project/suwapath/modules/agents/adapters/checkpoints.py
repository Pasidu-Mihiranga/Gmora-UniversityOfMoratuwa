"""LangGraph checkpointer on Postgres, schema `langgraph` (EVT-19, ADR-0004).

Checkpoints hold execution position only (which node, a pending interrupt, scratch state of the
current run). Facts are read fresh from the case log at every decision node (EVT-20, EVT-21).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

SCHEMA = "langgraph"

# Privacy first: LangGraph must never send traces to a hosted service.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"


def checkpoint_conninfo(database_url: str) -> str:
    """The app's database URL as a plain psycopg URL whose search path is the langgraph schema."""
    plain = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    separator = "&" if "?" in plain else "?"
    return f"{plain}{separator}options={quote(f'-csearch_path={SCHEMA}')}"


@asynccontextmanager
async def postgres_checkpointer(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    async with AsyncPostgresSaver.from_conn_string(checkpoint_conninfo(database_url)) as saver:
        await saver.setup()  # idempotent: creates or upgrades the checkpoint tables
        yield saver
