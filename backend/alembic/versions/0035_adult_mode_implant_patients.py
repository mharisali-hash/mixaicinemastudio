"""Adult mode opt-in on user; date of birth and patient accounts on implant_patient

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-22 18:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0035'
down_revision = '0034'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user', sa.Column('adult_mode_at', sa.DateTime(), nullable=True))
    op.add_column('user', sa.Column('adult_revoked_at', sa.DateTime(), nullable=True))
    op.add_column('user', sa.Column('adult_revoked_by', sa.String(36), nullable=True))

    # batch mode: SQLite can't add a foreign key with a plain ALTER
    with op.batch_alter_table('implant_patient') as b:
        b.add_column(sa.Column('date_of_birth', sa.Date(), nullable=True))
        b.add_column(sa.Column('patient_user_id', sa.String(36), nullable=True))
        b.add_column(sa.Column('invite_code_hash', sa.String(64), nullable=True))
        b.add_column(sa.Column('invite_expires_at', sa.DateTime(), nullable=True))
        b.create_foreign_key('fk_implant_patient_patient_user', 'user', ['patient_user_id'], ['id'],
                             ondelete='SET NULL')
        b.create_index('ix_implant_patient_patient_user_id', ['patient_user_id'])


def downgrade() -> None:
    with op.batch_alter_table('implant_patient') as b:
        b.drop_index('ix_implant_patient_patient_user_id')
        b.drop_constraint('fk_implant_patient_patient_user', type_='foreignkey')
        for c in ('invite_expires_at', 'invite_code_hash', 'patient_user_id', 'date_of_birth'):
            b.drop_column(c)
    for c in ('adult_revoked_by', 'adult_revoked_at', 'adult_mode_at'):
        op.drop_column('user', c)
