"""add fiber to food logs and goals

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("food_logs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fiber_g", sa.Float(), nullable=True))
    with op.batch_alter_table("user_goals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fiber_g", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("user_goals", schema=None) as batch_op:
        batch_op.drop_column("fiber_g")
    with op.batch_alter_table("food_logs", schema=None) as batch_op:
        batch_op.drop_column("fiber_g")
