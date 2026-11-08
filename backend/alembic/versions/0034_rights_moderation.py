"""Rights and moderation (contract v42): user rights/staff/block columns, manager_member, user_notice,
right_release, media_item review and removal columns

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-08 12:00:00

right_release starts empty; app.rights.ensure_baseline fills it on the first start after this, so every studio
and model that exists today stays as it is and only later additions wait for an admin.
"""
from alembic import op
import sqlalchemy as sa


revision = '0034'
down_revision = '0033'
branch_labels = None
depends_on = None

USER_COLS = ('staff_role', 'manager_scope', 'rights', 'blocked_at', 'blocked_reason', 'blocked_by')
MEDIA_COLS = ('reviewed_at', 'reviewed_by', 'removed_at', 'removed_by', 'removed_reason')


def upgrade() -> None:
    op.add_column('user', sa.Column('staff_role', sa.String(20), nullable=True))
    op.add_column('user', sa.Column('manager_scope', sa.String(10), nullable=False, server_default='site'))
    op.add_column('user', sa.Column('rights', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('user', sa.Column('blocked_at', sa.DateTime(), nullable=True))
    op.add_column('user', sa.Column('blocked_reason', sa.String(1000), nullable=False, server_default=''))
    op.add_column('user', sa.Column('blocked_by', sa.String(36), nullable=True))

    op.add_column('media_item', sa.Column('reviewed_at', sa.DateTime(), nullable=True))
    op.add_column('media_item', sa.Column('reviewed_by', sa.String(36), nullable=True))
    op.add_column('media_item', sa.Column('removed_at', sa.DateTime(), nullable=True))
    op.add_column('media_item', sa.Column('removed_by', sa.String(36), nullable=True))
    op.add_column('media_item', sa.Column('removed_reason', sa.String(1000), nullable=False, server_default=''))
    op.create_index('ix_media_item_removed_at', 'media_item', ['removed_at'])
    op.create_index('ix_media_item_reviewed_at', 'media_item', ['reviewed_at'])

    op.create_table(
        'manager_member',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('manager_user_id', sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('manager_user_id', 'user_id'),
    )
    op.create_index('ix_manager_member_manager_user_id', 'manager_member', ['manager_user_id'])
    op.create_index('ix_manager_member_user_id', 'manager_member', ['user_id'])
    op.create_table(
        'user_notice',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('reason', sa.String(1000), nullable=False),
        sa.Column('item_type', sa.String(20), nullable=False, server_default=''),
        sa.Column('item_id', sa.String(36), nullable=True),
        sa.Column('created_by', sa.String(36), nullable=True),
        sa.Column('emailed', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_user_notice_user_id', 'user_notice', ['user_id'])
    op.create_index('ix_user_notice_created_at', 'user_notice', ['created_at'])
    op.create_table(
        'right_release',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('key', sa.String(120), nullable=False),
        sa.Column('released_by', sa.String(36), nullable=True),
        sa.Column('released_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('kind', 'key'),
    )


def downgrade() -> None:
    op.drop_table('right_release')
    op.drop_index('ix_user_notice_created_at', 'user_notice')
    op.drop_index('ix_user_notice_user_id', 'user_notice')
    op.drop_table('user_notice')
    op.drop_index('ix_manager_member_user_id', 'manager_member')
    op.drop_index('ix_manager_member_manager_user_id', 'manager_member')
    op.drop_table('manager_member')
    op.drop_index('ix_media_item_reviewed_at', 'media_item')
    op.drop_index('ix_media_item_removed_at', 'media_item')
    # plain DROP COLUMN, not batch mode: see 0022
    for col in MEDIA_COLS:
        op.execute(f'ALTER TABLE media_item DROP COLUMN {col}')
    for col in USER_COLS:
        op.execute(f'ALTER TABLE "user" DROP COLUMN {col}')
