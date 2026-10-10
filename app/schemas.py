from collections.abc import Iterable
from datetime import date, datetime
from typing import Annotated, Literal, Protocol, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

MealType = Literal["breakfast", "lunch", "dinner", "snack"]
SessionKind = Literal["fast", "eat"]

NonNegative = Field(default=None, ge=0)

HOURS_PER_DAY = 24
# The daily schedule always leaves at least an hour to fast and to eat.
MIN_FAST_HOURS = 1
MAX_FAST_HOURS = HOURS_PER_DAY - 1
MAX_SESSION_HOURS = 240
MAX_DISHES_PER_MEAL = 50
MAX_SERVINGS = 100

TargetHours = Field(default=None, gt=0, le=MAX_SESSION_HOURS)


def _check_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as err:
        raise ValueError(f"unknown timezone {value!r}") from err
    return value


Timezone = Annotated[str, AfterValidator(_check_timezone)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth ---------------------------------------------------------------


class GoogleLoginIn(BaseModel):
    id_token: str


class RefreshIn(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int


# --- user / goals -------------------------------------------------------


class GoalsOut(ORMModel):
    daily_calories: float | None
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    fiber_g: float | None
    default_fast_hours: float

    @computed_field  # type: ignore[prop-decorator]
    @property
    def eating_window_hours(self) -> float:
        """The rest of the day after the daily fast, e.g. 16h fasting -> 8h eating."""
        return max(HOURS_PER_DAY - self.default_fast_hours, 0)


class GoalsIn(BaseModel):
    daily_calories: float | None = NonNegative
    protein_g: float | None = NonNegative
    carbs_g: float | None = NonNegative
    fat_g: float | None = NonNegative
    fiber_g: float | None = NonNegative
    default_fast_hours: float | None = Field(default=None, ge=MIN_FAST_HOURS, le=MAX_FAST_HOURS)


class UserOut(ORMModel):
    id: int
    email: str
    name: str | None
    avatar_url: str | None
    timezone: str
    onboarded_at: datetime | None
    goals: GoalsOut


class UserPatch(BaseModel):
    name: str | None = None
    timezone: Timezone | None = None
    goals: GoalsIn | None = None


class OnboardingSession(BaseModel):
    """The session the user is in at setup: when it started, or (eating only) when they plan
    to start fasting, in which case the eating window is counted from the start of today."""

    kind: SessionKind
    started_at: AwareDatetime | None = None
    fast_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _one_anchor(self) -> Self:
        if (self.started_at is None) == (self.fast_at is None):
            raise ValueError("give exactly one of started_at or fast_at")
        if self.fast_at is not None and self.kind != "eat":
            raise ValueError("fast_at is only valid for an eating session")
        return self


class OnboardingIn(BaseModel):
    timezone: Timezone
    goals: GoalsIn
    current: OnboardingSession


# --- sessions -----------------------------------------------------------


class SessionSwitch(BaseModel):
    kind: SessionKind
    at: AwareDatetime | None = None
    target_hours: float | None = TargetHours
    notes: str | None = None


class SessionPatch(BaseModel):
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    target_hours: float | None = TargetHours
    notes: str | None = None


class SessionOut(ORMModel):
    id: int
    kind: SessionKind
    started_at: datetime
    ended_at: datetime | None
    target_hours: float
    notes: str | None


# --- nutrition ----------------------------------------------------------


class Totals(BaseModel):
    calories: float = 0
    protein_g: float = 0
    carbs_g: float = 0
    fat_g: float = 0
    fiber_g: float = 0


class Nutrition(BaseModel):
    """Nutrition for one serving; any value may be unknown."""

    calories: float | None = NonNegative
    protein_g: float | None = NonNegative
    carbs_g: float | None = NonNegative
    fat_g: float | None = NonNegative
    fiber_g: float | None = NonNegative


TOTAL_FIELDS = tuple(Totals.model_fields)
# Rounded so float noise (0.1 + 0.2 = 0.30000000000000004) never reaches the client.
TOTALS_DECIMALS = 2


class NutritionLike(Protocol):
    @property
    def calories(self) -> float | None: ...
    @property
    def protein_g(self) -> float | None: ...
    @property
    def carbs_g(self) -> float | None: ...
    @property
    def fat_g(self) -> float | None: ...
    @property
    def fiber_g(self) -> float | None: ...


def sum_totals(parts: Iterable[tuple[NutritionLike, float]]) -> Totals:
    """Total nutrition of (per-serving nutrition, servings) pairs; unknown values count as 0."""
    sums = dict.fromkeys(TOTAL_FIELDS, 0.0)
    for nutrition, servings in parts:
        for field in TOTAL_FIELDS:
            sums[field] += (getattr(nutrition, field) or 0) * servings
    return Totals(**{field: round(value, TOTALS_DECIMALS) for field, value in sums.items()})


Name = Field(min_length=1, max_length=255)
Unit = Field(default=None, max_length=32)
Servings = Field(default=1, gt=0, le=MAX_SERVINGS)
MealItems = Field(min_length=1, max_length=MAX_DISHES_PER_MEAL)


# --- dish library -------------------------------------------------------


class DishIn(Nutrition):
    name: str = Name
    quantity: float | None = NonNegative
    unit: str | None = Unit


class DishPatch(Nutrition):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    quantity: float | None = NonNegative
    unit: str | None = Unit


class DishOut(DishIn, ORMModel):
    id: int


# --- saved meal library -------------------------------------------------


class SavedMealItemIn(BaseModel):
    dish_id: int
    servings: float = Servings


class SavedMealIn(BaseModel):
    name: str = Name
    items: list[SavedMealItemIn] = MealItems


class SavedMealPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    # Replaces all of the meal's dishes when given.
    items: list[SavedMealItemIn] | None = Field(
        default=None, min_length=1, max_length=MAX_DISHES_PER_MEAL
    )


class SavedMealItemOut(ORMModel):
    dish: DishOut
    servings: float


class SavedMealOut(ORMModel):
    id: int
    name: str
    items: list[SavedMealItemOut]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def totals(self) -> Totals:
        return sum_totals((item.dish, item.servings) for item in self.items)


# --- logged meals -------------------------------------------------------


class MealItemIn(Nutrition):
    name: str = Name
    quantity: float | None = NonNegative
    unit: str | None = Unit
    servings: float = Servings


class MealItemOut(MealItemIn, ORMModel):
    pass


class MealIn(BaseModel):
    eaten_at: AwareDatetime | None = None
    name: str | None = Field(default=None, max_length=255)
    meal_type: MealType
    notes: str | None = None
    items: list[MealItemIn] = MealItems


class MealPatch(BaseModel):
    eaten_at: AwareDatetime | None = None
    name: str | None = Field(default=None, max_length=255)
    meal_type: MealType | None = None
    notes: str | None = None
    # Replaces all of the meal's dishes when given.
    items: list[MealItemIn] | None = Field(
        default=None, min_length=1, max_length=MAX_DISHES_PER_MEAL
    )


class MealOut(ORMModel):
    id: int
    eaten_at: datetime
    name: str | None
    meal_type: MealType
    notes: str | None
    items: list[MealItemOut]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def totals(self) -> Totals:
        return sum_totals((item, item.servings) for item in self.items)


# --- summary ------------------------------------------------------------


class Streak(BaseModel):
    # Consecutive days, ending today or yesterday, on which a fast that reached its goal ended.
    days: int


class DailySummary(BaseModel):
    date: date
    totals: Totals
    goals: GoalsOut
    entry_count: int  # meals logged that day
    fasting_hours: float
