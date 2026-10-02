from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field, field_validator

MealType = Literal["breakfast", "lunch", "dinner", "snack"]

NonNegative = Field(default=None, ge=0)

HOURS_PER_DAY = 24
# The daily schedule always leaves at least an hour to fast and to eat.
MIN_FAST_HOURS = 1
MAX_FAST_HOURS = HOURS_PER_DAY - 1


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
    default_fast_hours: float | None = Field(default=None, ge=MIN_FAST_HOURS, le=MAX_FAST_HOURS)


class UserOut(ORMModel):
    id: int
    email: str
    name: str | None
    avatar_url: str | None
    timezone: str
    goals: GoalsOut


class UserPatch(BaseModel):
    name: str | None = None
    timezone: str | None = None
    goals: GoalsIn | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as err:
                raise ValueError(f"unknown timezone {value!r}") from err
        return value


# --- fasts --------------------------------------------------------------


class FastStart(BaseModel):
    started_at: AwareDatetime | None = None
    target_hours: float | None = Field(default=None, gt=0, le=240)
    notes: str | None = None


class FastEnd(BaseModel):
    ended_at: AwareDatetime | None = None


class FastPatch(BaseModel):
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    target_hours: float | None = Field(default=None, gt=0, le=240)
    notes: str | None = None


class FastOut(ORMModel):
    id: int
    started_at: datetime
    ended_at: datetime | None
    target_hours: float
    notes: str | None


# --- food ---------------------------------------------------------------


class FoodBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    meal_type: MealType
    quantity: float | None = NonNegative
    unit: str | None = Field(default=None, max_length=32)
    calories: float | None = NonNegative
    protein_g: float | None = NonNegative
    carbs_g: float | None = NonNegative
    fat_g: float | None = NonNegative
    notes: str | None = None


class FoodIn(FoodBase):
    eaten_at: AwareDatetime | None = None


class FoodPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    meal_type: MealType | None = None
    eaten_at: AwareDatetime | None = None
    quantity: float | None = NonNegative
    unit: str | None = Field(default=None, max_length=32)
    calories: float | None = NonNegative
    protein_g: float | None = NonNegative
    carbs_g: float | None = NonNegative
    fat_g: float | None = NonNegative
    notes: str | None = None


class FoodOut(FoodBase, ORMModel):
    id: int
    eaten_at: datetime


# --- summary ------------------------------------------------------------


class Totals(BaseModel):
    calories: float = 0
    protein_g: float = 0
    carbs_g: float = 0
    fat_g: float = 0


class DailySummary(BaseModel):
    date: date
    totals: Totals
    goals: GoalsOut
    entry_count: int
    fasting_hours: float
