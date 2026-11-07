"""Character Looks: character_look, character.performance

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-07 21:00:00

A look is an outfit, setting and pose of a studio character with its own pictures (generations with
target_type "character_look"). Performance is how the character moves on camera.
"""
from alembic import op
import sqlalchemy as sa


revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('character') as batch:
        batch.add_column(sa.Column('performance', sa.JSON(), nullable=False, server_default='{}'))
    op.create_table(
        'character_look',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('workspace_id', sa.String(36), sa.ForeignKey('workspace.id', ondelete='CASCADE'), nullable=False),
        sa.Column('character_id', sa.String(36), sa.ForeignKey('character.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('outfit', sa.Text(), nullable=False),
        sa.Column('setting', sa.Text(), nullable=False),
        sa.Column('pose', sa.Text(), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False),
        sa.Column('sort', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_character_look_workspace_id', 'character_look', ['workspace_id'])
    op.create_index('ix_character_look_character_id', 'character_look', ['character_id'])


def downgrade() -> None:
    op.drop_index('ix_character_look_character_id', 'character_look')
    op.drop_index('ix_character_look_workspace_id', 'character_look')
    op.drop_table('character_look')
    with op.batch_alter_table('character') as batch:
        batch.drop_column('performance')
