"""notification delivery time

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-28 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'notifications_items',
        sa.Column('deliver_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.alter_column('notifications_items', 'deliver_at', server_default=None)
    op.create_index('ix_notifications_items_delivery', 'notifications_items', ['user_id', 'deliver_at', 'id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_notifications_items_delivery', table_name='notifications_items')
    op.drop_column('notifications_items', 'deliver_at')
