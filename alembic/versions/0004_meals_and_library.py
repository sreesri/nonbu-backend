"""food logs become meals of one or more dishes; add the dish and saved-meal library

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08 12:00:00.000000

Each existing food entry becomes a meal holding that one dish, and the library is seeded
with one dish per distinct food name (from its most recent entry).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUTRITION = ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g")
# Columns that move from the food entry to its single dish.
DISH_COLUMNS = ("quantity", "unit", *NUTRITION)


def _timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _nutrition() -> list[sa.Column[sa.Float]]:
    return [sa.Column(name, sa.Float(), nullable=True) for name in NUTRITION]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "dishes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        *_nutrition(),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dishes_user", "dishes", ["user_id"])

    op.create_table(
        "saved_meals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_saved_meals_user", "saved_meals", ["user_id"])

    op.create_table(
        "saved_meal_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("saved_meal_id", sa.Integer(), nullable=False),
        sa.Column("dish_id", sa.Integer(), nullable=False),
        sa.Column("servings", sa.Float(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["saved_meal_id"], ["saved_meals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dish_id"], ["dishes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_saved_meal_items_saved_meal_id", "saved_meal_items", ["saved_meal_id"])
    op.create_index("ix_saved_meal_items_dish_id", "saved_meal_items", ["dish_id"])

    # Renamed rather than recreated so existing ids (and the id sequence) carry over.
    op.drop_index("ix_food_logs_user_eaten", table_name="food_logs")
    op.rename_table("food_logs", "meal_logs")
    op.create_index("ix_meal_logs_user_eaten", "meal_logs", ["user_id", "eaten_at"])

    op.create_table(
        "meal_log_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("meal_log_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("servings", sa.Float(), nullable=False),
        *_nutrition(),
        sa.ForeignKeyConstraint(["meal_log_id"], ["meal_logs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meal_log_items_meal_log_id", "meal_log_items", ["meal_log_id"])

    columns = ", ".join(DISH_COLUMNS)
    op.execute(
        f"INSERT INTO meal_log_items (meal_log_id, position, name, servings, {columns}) "  # noqa: S608 - fixed column names
        f"SELECT id, 0, name, 1, {columns} FROM meal_logs"
    )
    op.execute(
        f"INSERT INTO dishes (user_id, name, {columns}) "  # noqa: S608 - fixed column names
        f"SELECT user_id, TRIM(name), {columns} FROM ("
        f"  SELECT user_id, name, {columns}, ROW_NUMBER() OVER ("
        "    PARTITION BY user_id, LOWER(TRIM(name)) ORDER BY eaten_at DESC, id DESC"
        "  ) AS newest FROM meal_logs"
        ") AS entries WHERE newest = 1"
    )

    with op.batch_alter_table("meal_logs", schema=None) as batch_op:
        batch_op.alter_column("name", existing_type=sa.String(length=255), nullable=True)
        for column in DISH_COLUMNS:
            batch_op.drop_column(column)
    # The food's name now lives on its dish; a meal's own name is optional.
    op.execute("UPDATE meal_logs SET name = NULL")


def downgrade() -> None:
    """Downgrade schema. Lossy: each meal collapses into one entry with its summed nutrition."""
    with op.batch_alter_table("meal_logs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("quantity", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("unit", sa.String(length=32), nullable=True))
        for column in _nutrition():
            batch_op.add_column(column)

    items = "FROM meal_log_items WHERE meal_log_items.meal_log_id = meal_logs.id"
    sums = ", ".join(f"{n} = (SELECT SUM({n} * servings) {items})" for n in NUTRITION)
    op.execute(
        "UPDATE meal_logs SET "  # noqa: S608 - fixed column names
        f"name = COALESCE(name, (SELECT name {items} ORDER BY position LIMIT 1), 'Meal'), {sums}"
    )
    with op.batch_alter_table("meal_logs", schema=None) as batch_op:
        batch_op.alter_column("name", existing_type=sa.String(length=255), nullable=False)

    op.drop_index("ix_meal_log_items_meal_log_id", table_name="meal_log_items")
    op.drop_table("meal_log_items")
    op.drop_index("ix_meal_logs_user_eaten", table_name="meal_logs")
    op.rename_table("meal_logs", "food_logs")
    op.create_index("ix_food_logs_user_eaten", "food_logs", ["user_id", "eaten_at"])

    op.drop_index("ix_saved_meal_items_dish_id", table_name="saved_meal_items")
    op.drop_index("ix_saved_meal_items_saved_meal_id", table_name="saved_meal_items")
    op.drop_table("saved_meal_items")
    op.drop_index("ix_saved_meals_user", table_name="saved_meals")
    op.drop_table("saved_meals")
    op.drop_index("ix_dishes_user", table_name="dishes")
    op.drop_table("dishes")
