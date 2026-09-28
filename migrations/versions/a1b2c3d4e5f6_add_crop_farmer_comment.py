"""add_crop_farmer_comment

Revision ID: a1b2c3d4e5f6
Revises: 9b2c3d4e5f6a
Create Date: 2026-09-28 10:05:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '9b2c3d4e5f6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE crops ADD COLUMN IF NOT EXISTS farmer_comment TEXT;")


def downgrade() -> None:
    op.execute("ALTER TABLE crops DROP COLUMN IF EXISTS farmer_comment;")
