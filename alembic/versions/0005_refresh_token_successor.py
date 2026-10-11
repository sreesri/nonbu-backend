"""link each rotated refresh token to its replacement

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-11 12:00:00.000000

Lets a refresh be retried when its response never reached the app.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK = "fk_refresh_tokens_replaced_by_id"


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.add_column(sa.Column("replaced_by_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            FK, "refresh_tokens", ["replaced_by_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.drop_constraint(FK, type_="foreignkey")
        batch_op.drop_column("replaced_by_id")
