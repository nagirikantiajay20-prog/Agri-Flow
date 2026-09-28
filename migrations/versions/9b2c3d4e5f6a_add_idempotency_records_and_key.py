"""add_idempotency_records_and_key

Revision ID: 9b2c3d4e5f6a
Revises: 8a1e2f3d4c5b
Create Date: 2026-09-25 16:15:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = '9b2c3d4e5f6a'
down_revision: Union[str, None] = '8a1e2f3d4c5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add idempotency_records table
    op.create_table(
        'idempotency_records',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('operation', sa.String(length=64), nullable=False),
        sa.Column('request_hash', sa.String(length=64), nullable=False),
        sa.Column('response_code', sa.Integer(), nullable=False, server_default='201'),
        sa.Column('response_data', JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('user_id', 'idempotency_key', name='uq_idempotency_user_key'),
    )
    op.create_index('ix_idempotency_user_key', 'idempotency_records', ['user_id', 'idempotency_key'])

    # 2. Add idempotency_key to seed_purchases
    op.add_column('seed_purchases', sa.Column('idempotency_key', sa.String(length=128), nullable=True))
    op.create_index('ix_seed_purchases_idempotency_key', 'seed_purchases', ['idempotency_key'])


def downgrade() -> None:
    op.drop_index('ix_seed_purchases_idempotency_key', table_name='seed_purchases')
    op.drop_column('seed_purchases', 'idempotency_key')
    op.drop_index('ix_idempotency_user_key', table_name='idempotency_records')
    op.drop_table('idempotency_records')
