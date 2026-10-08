from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    func,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetime stored as UTC; always returned tz-aware (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # Null until the first-run setup (schedule, current session, goals) is completed.
    onboarded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    goals: Mapped["UserGoals"] = relationship(
        back_populates="user", lazy="selectin", cascade="all, delete-orphan"
    )


class UserGoals(Base):
    __tablename__ = "user_goals"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    daily_calories: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    fiber_g: Mapped[float | None] = mapped_column(Float)
    default_fast_hours: Mapped[float] = mapped_column(Float, default=16)

    user: Mapped[User] = relationship(back_populates="goals")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now()
    )


class TimelineSession(TimestampMixin, Base):
    """A fasting or eating session. Switching closes the open one and opens the other kind
    at the same instant, so sessions form a contiguous, non-overlapping timeline."""

    __tablename__ = "sessions"
    __table_args__ = (
        # At most one open (not yet ended) session per user.
        Index(
            "uq_sessions_one_open_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
            sqlite_where=text("ended_at IS NULL"),
        ),
        Index("ix_sessions_user_started", "user_id", "started_at"),
        CheckConstraint("kind IN ('fast', 'eat')", name="ck_sessions_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(8))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    target_hours: Mapped[float] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)


class NutritionMixin:
    """Nutrition for one serving; any value may be unknown."""

    calories: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    fiber_g: Mapped[float | None] = mapped_column(Float)


class Dish(NutritionMixin, TimestampMixin, Base):
    """A dish in the user's library; `quantity` + `unit` describe one serving (e.g. 2 pc)."""

    __tablename__ = "dishes"
    __table_args__ = (Index("ix_dishes_user", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))


class SavedMeal(TimestampMixin, Base):
    """A named combination of library dishes, for logging the same meal again in one tap.
    Its nutrition follows the dishes, so editing a dish updates every meal that uses it."""

    __tablename__ = "saved_meals"
    __table_args__ = (Index("ix_saved_meals_user", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))

    items: Mapped[list["SavedMealItem"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="SavedMealItem.position"
    )


class SavedMealItem(Base):
    __tablename__ = "saved_meal_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    saved_meal_id: Mapped[int] = mapped_column(
        ForeignKey("saved_meals.id", ondelete="CASCADE"), index=True
    )
    # Deleting a dish drops it from the saved meals that use it.
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id", ondelete="CASCADE"), index=True)
    servings: Mapped[float] = mapped_column(Float, default=1)
    position: Mapped[int] = mapped_column(Integer, default=0)

    dish: Mapped[Dish] = relationship(lazy="selectin")


class MealLog(TimestampMixin, Base):
    """A meal the user ate: one or more dishes at one time."""

    __tablename__ = "meal_logs"
    __table_args__ = (Index("ix_meal_logs_user_eaten", "user_id", "eaten_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    eaten_at: Mapped[datetime] = mapped_column(UTCDateTime)
    name: Mapped[str | None] = mapped_column(String(255))
    meal_type: Mapped[str] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["MealLogItem"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="MealLogItem.position"
    )


class MealLogItem(NutritionMixin, Base):
    """A dish as eaten in a logged meal. Copied from the library rather than linked, so
    editing or deleting a library dish never rewrites what was already logged."""

    __tablename__ = "meal_log_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meal_log_id: Mapped[int] = mapped_column(
        ForeignKey("meal_logs.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))
    servings: Mapped[float] = mapped_column(Float, default=1)
