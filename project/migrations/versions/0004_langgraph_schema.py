"""langgraph schema for agent checkpoints (EVT-19, DB-1)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-28 08:30:00.000000

The checkpointer creates its own tables in this schema at startup (`main.py agents`). They hold
execution position only; facts live in the case event log.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")
