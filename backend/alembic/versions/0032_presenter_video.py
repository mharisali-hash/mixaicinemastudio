"""Presenter Video: presenter_video

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-08 10:00:00

A character in one of its looks presenting a script, scene by scene. Scene pictures, voice takes and video
takes are generations with target_type "presenter_scene"; the finished cut is a Library video.
"""
from alembic import op
import sqlalchemy as sa


revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'presenter_video',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('workspace_id', sa.String(36), sa.ForeignKey('workspace.id', ondelete='CASCADE'), nullable=False),
        sa.Column('character_id', sa.String(36), sa.ForeignKey('character.id', ondelete='SET NULL'), nullable=True),
        sa.Column('look_id', sa.String(36), nullable=True),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('language', sa.String(12), nullable=False),
        sa.Column('settings', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('scenes', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('stages', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('outputs', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('job_id', sa.String(36), nullable=True),
        sa.Column('created_by', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_presenter_video_workspace_id', 'presenter_video', ['workspace_id'])


def downgrade() -> None:
    op.drop_index('ix_presenter_video_workspace_id', 'presenter_video')
    op.drop_table('presenter_video')
