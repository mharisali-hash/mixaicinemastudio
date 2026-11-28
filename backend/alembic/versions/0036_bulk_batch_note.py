"""Why a bulk batch stopped by itself (ComfyUI down), shown on its page

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-28 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0036'
down_revision = '0035'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('bulk_batch', sa.Column('note', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('bulk_batch') as b:
        b.drop_column('note')
