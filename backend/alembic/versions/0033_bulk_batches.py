"""Admin bulk runs: bulk_batch, bulk_item

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-08 14:00:00

An admin uploads thousands of pictures and runs one action on all of them (remove background, or an
Image Edit instruction) to build training datasets. The pictures and their results live in a workspace of
their own, so no Library or job list sees them.
"""
from alembic import op
import sqlalchemy as sa


revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'bulk_batch',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('workspace_id', sa.String(36), sa.ForeignKey('workspace.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('options', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('run_job_id', sa.String(36), nullable=True),
        sa.Column('exports', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_bulk_batch_workspace_id', 'bulk_batch', ['workspace_id'])
    op.create_index('ix_bulk_batch_user_id', 'bulk_batch', ['user_id'])
    op.create_table(
        'bulk_item',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('batch_id', sa.String(36), sa.ForeignKey('bulk_batch.id', ondelete='CASCADE'), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(500), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=True),
        sa.Column('media_id', sa.String(36), nullable=True),
        sa.Column('source_gen_id', sa.String(36), nullable=True),
        sa.Column('instruction', sa.Text(), nullable=True),
        sa.Column('keywords', sa.Text(), nullable=True),
        sa.Column('result_gen_id', sa.String(36), nullable=True),
        sa.Column('job_id', sa.String(36), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('prompt_used', sa.Text(), nullable=True),
        sa.Column('sample', sa.Boolean(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('batch_id', 'filename', 'size', name='uq_bulk_item_file'),
    )
    op.create_index('ix_bulk_item_batch_id', 'bulk_item', ['batch_id'])
    op.create_index('ix_bulk_item_job_id', 'bulk_item', ['job_id'])
    op.create_index('ix_bulk_item_batch_status', 'bulk_item', ['batch_id', 'status'])


def downgrade() -> None:
    op.drop_index('ix_bulk_item_batch_status', 'bulk_item')
    op.drop_index('ix_bulk_item_job_id', 'bulk_item')
    op.drop_index('ix_bulk_item_batch_id', 'bulk_item')
    op.drop_table('bulk_item')
    op.drop_index('ix_bulk_batch_user_id', 'bulk_batch')
    op.drop_index('ix_bulk_batch_workspace_id', 'bulk_batch')
    op.drop_table('bulk_batch')
