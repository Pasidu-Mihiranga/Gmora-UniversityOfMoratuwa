"""verifier reviews: case, patient, sealed preview

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-28 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('verifier_verdicts', sa.Column('case_id', sa.Uuid(), nullable=True))
    # No review rows exist before this revision (the verifier had no review queue), so the new
    # required columns need no backfill.
    op.add_column('verifier_reviews', sa.Column('case_id', sa.Uuid(), nullable=False))
    op.add_column('verifier_reviews', sa.Column('patient_id', sa.Uuid(), nullable=False))
    op.add_column('verifier_reviews', sa.Column('preview_enc', sa.LargeBinary(), nullable=False))
    op.create_index('ix_verifier_reviews_pending', 'verifier_reviews', ['reviewer_id', 'created_at'], unique=False, postgresql_where=sa.text("status = 'pending'"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_verifier_reviews_pending', table_name='verifier_reviews', postgresql_where=sa.text("status = 'pending'"))
    op.drop_column('verifier_reviews', 'preview_enc')
    op.drop_column('verifier_reviews', 'patient_id')
    op.drop_column('verifier_reviews', 'case_id')
    op.drop_column('verifier_verdicts', 'case_id')
