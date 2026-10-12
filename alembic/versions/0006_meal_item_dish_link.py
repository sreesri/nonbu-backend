"""link logged dishes to the library dish they came from

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12 12:00:00.000000

Existing logged dishes are linked to the user's library dish of the same name, if any.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK = "fk_meal_log_items_dish_id"
INDEX = "ix_meal_log_items_dish_id"


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("meal_log_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("dish_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(FK, "dishes", ["dish_id"], ["id"], ondelete="SET NULL")
        batch_op.create_index(INDEX, ["dish_id"])
    op.execute(
        "UPDATE meal_log_items SET dish_id = ("
        "  SELECT MIN(dishes.id) FROM dishes"
        "  JOIN meal_logs ON meal_logs.user_id = dishes.user_id"
        "  WHERE meal_logs.id = meal_log_items.meal_log_id"
        "  AND LOWER(TRIM(dishes.name)) = LOWER(TRIM(meal_log_items.name))"
        ")"
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("meal_log_items", schema=None) as batch_op:
        batch_op.drop_index(INDEX)
        batch_op.drop_constraint(FK, type_="foreignkey")
        batch_op.drop_column("dish_id")
