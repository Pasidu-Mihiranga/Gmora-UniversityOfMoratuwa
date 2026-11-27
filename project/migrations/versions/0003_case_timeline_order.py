"""case timeline: access category and a stable order

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-27 21:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cases_timeline",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
    )
    op.add_column("cases_timeline", sa.Column("category", sa.String(length=40), nullable=False))
    op.create_unique_constraint(op.f("uq_cases_timeline_seq"), "cases_timeline", ["seq"])
    op.create_index("ix_cases_timeline_case_seq", "cases_timeline", ["case_id", "seq"])


def downgrade() -> None:
    op.drop_index("ix_cases_timeline_case_seq", table_name="cases_timeline")
    op.drop_constraint(op.f("uq_cases_timeline_seq"), "cases_timeline", type_="unique")
    op.drop_column("cases_timeline", "category")
    op.drop_column("cases_timeline", "seq")
