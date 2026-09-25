"""seed_grade_pricing_and_limits

Revision ID: 8a1e2f3d4c5b
Revises: 79d01a1cc40b
Create Date: 2026-09-25 14:50:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8a1e2f3d4c5b'
down_revision: Union[str, None] = '79d01a1cc40b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('seeds', sa.Column('price_grade_a', sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column('seeds', sa.Column('price_grade_b', sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column('seeds', sa.Column('price_grade_c', sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column(
        'seeds',
        sa.Column('max_order_quantity_kg', sa.Numeric(precision=14, scale=2), server_default='50.0', nullable=True)
    )


def downgrade() -> None:
    op.drop_column('seeds', 'max_order_quantity_kg')
    op.drop_column('seeds', 'price_grade_c')
    op.drop_column('seeds', 'price_grade_b')
    op.drop_column('seeds', 'price_grade_a')
