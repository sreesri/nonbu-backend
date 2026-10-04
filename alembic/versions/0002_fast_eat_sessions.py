"""fasts become fast/eat sessions; add users.onboarded_at

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04 10:00:00.000000

"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOURS_PER_DAY = 24
# A longer gap between fasts is untracked time, not an eating session.
MAX_BACKFILLED_EAT_GAP = timedelta(hours=48)

sessions = sa.table(
    "sessions",
    sa.column("id", sa.Integer),
    sa.column("user_id", sa.Integer),
    sa.column("kind", sa.String),
    sa.column("started_at", sa.DateTime(timezone=True)),
    sa.column("ended_at", sa.DateTime(timezone=True)),
    sa.column("target_hours", sa.Float),
)
user_goals = sa.table(
    "user_goals",
    sa.column("user_id", sa.Integer),
    sa.column("default_fast_hours", sa.Float),
)
users = sa.table("users", sa.column("onboarded_at", sa.DateTime(timezone=True)))


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; everything is stored as UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _backfill_eat_sessions(conn: sa.Connection) -> None:
    """Add an eating session in each short gap between fasts, plus an open one after the
    latest fast if it ended recently, so existing users start on a contiguous timeline."""
    now = datetime.now(UTC)
    eat_hours = dict(
        conn.execute(
            sa.select(user_goals.c.user_id, HOURS_PER_DAY - user_goals.c.default_fast_hours)
        ).all()
    )
    fasts = conn.execute(
        sa.select(sessions.c.user_id, sessions.c.started_at, sessions.c.ended_at).order_by(
            sessions.c.user_id, sessions.c.started_at
        )
    ).all()

    rows = []
    for i, fast in enumerate(fasts):
        if fast.ended_at is None:
            continue
        ended_at = _aware(fast.ended_at)
        nxt = fasts[i + 1] if i + 1 < len(fasts) else None
        next_start = (
            _aware(nxt.started_at) if nxt is not None and nxt.user_id == fast.user_id else None
        )
        gap_end = next_start or now
        if timedelta(0) < gap_end - ended_at <= MAX_BACKFILLED_EAT_GAP:
            rows.append(
                {
                    "user_id": fast.user_id,
                    "kind": "eat",
                    "started_at": ended_at,
                    "ended_at": next_start,
                    "target_hours": eat_hours[fast.user_id],
                }
            )
    if rows:
        op.bulk_insert(sessions, rows)


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("fasts", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_fasts_one_open_per_user",
            postgresql_where=sa.text("ended_at IS NULL"),
            sqlite_where=sa.text("ended_at IS NULL"),
        )
        batch_op.drop_index("ix_fasts_user_started")
    op.rename_table("fasts", "sessions")
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.String(length=8), nullable=False, server_default="fast")
        )
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        # The default only existed to label the existing rows as fasts.
        batch_op.alter_column("kind", server_default=None)
        batch_op.create_check_constraint("ck_sessions_kind", "kind IN ('fast', 'eat')")
        batch_op.create_index("ix_sessions_user_started", ["user_id", "started_at"], unique=False)
        batch_op.create_index(
            "uq_sessions_one_open_per_user",
            ["user_id"],
            unique=True,
            postgresql_where=sa.text("ended_at IS NULL"),
            sqlite_where=sa.text("ended_at IS NULL"),
        )
    _backfill_eat_sessions(op.get_bind())

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True))
    # Existing users set themselves up before onboarding existed.
    op.execute(users.update().values(onboarded_at=sa.func.now()))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("onboarded_at")

    op.execute(sessions.delete().where(sessions.c.kind == "eat"))
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_sessions_one_open_per_user",
            postgresql_where=sa.text("ended_at IS NULL"),
            sqlite_where=sa.text("ended_at IS NULL"),
        )
        batch_op.drop_index("ix_sessions_user_started")
        batch_op.drop_constraint("ck_sessions_kind", type_="check")
        batch_op.drop_column("kind")
    op.rename_table("sessions", "fasts")
    with op.batch_alter_table("fasts", schema=None) as batch_op:
        batch_op.create_index("ix_fasts_user_started", ["user_id", "started_at"], unique=False)
        batch_op.create_index(
            "uq_fasts_one_open_per_user",
            ["user_id"],
            unique=True,
            postgresql_where=sa.text("ended_at IS NULL"),
            sqlite_where=sa.text("ended_at IS NULL"),
        )
