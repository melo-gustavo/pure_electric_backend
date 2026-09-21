"""add orders status created_at index

Revision ID: 3c1f0a7d9e42
Revises: fd5b9b6a99d5
Create Date: 2026-09-21 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '3c1f0a7d9e42'
down_revision: Union[str, Sequence[str], None] = 'fd5b9b6a99d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index('ix_orders_status_created_at', 'orders', ['status', 'created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_orders_status_created_at', table_name='orders')
