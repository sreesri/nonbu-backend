from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.auth import CurrentUser, Session
from app.models import MealLog, MealLogItem, User, utcnow
from app.schemas import MealIn, MealItemIn, MealOut, MealPatch
from app.timeutil import day_bounds, local_today

router = APIRouter(prefix="/meals", tags=["meals"])

# Fields that must not be cleared by a PATCH with null.
_REQUIRED = {"meal_type", "eaten_at", "items"}


async def _get_owned(db: Session, user: User, meal_id: int) -> MealLog:
    meal = await db.get(MealLog, meal_id)
    if meal is None or meal.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meal not found")
    return meal


def _items(items: list[MealItemIn]) -> list[MealLogItem]:
    return [MealLogItem(position=i, **item.model_dump()) for i, item in enumerate(items)]


async def meals_between(db: Session, user: User, start: datetime, end: datetime) -> list[MealLog]:
    result = await db.scalars(
        select(MealLog)
        .where(MealLog.user_id == user.id, MealLog.eaten_at >= start, MealLog.eaten_at < end)
        .order_by(MealLog.eaten_at)
    )
    return list(result)


@router.get("", response_model=list[MealOut])
async def list_meals(
    user: CurrentUser, db: Session, day: date | None = Query(None, alias="date")
) -> list[MealOut]:
    start, end = day_bounds(day or local_today(user.timezone), user.timezone)
    return [MealOut.model_validate(m) for m in await meals_between(db, user, start, end)]


@router.post("", response_model=MealOut, status_code=status.HTTP_201_CREATED)
async def create_meal(body: MealIn, user: CurrentUser, db: Session) -> MealOut:
    meal = MealLog(
        user_id=user.id,
        eaten_at=body.eaten_at or utcnow(),
        name=body.name,
        meal_type=body.meal_type,
        notes=body.notes,
        items=_items(body.items),
    )
    db.add(meal)
    await db.commit()
    return MealOut.model_validate(meal)


@router.get("/{meal_id}", response_model=MealOut)
async def get_meal(meal_id: int, user: CurrentUser, db: Session) -> MealOut:
    return MealOut.model_validate(await _get_owned(db, user, meal_id))


@router.patch("/{meal_id}", response_model=MealOut)
async def patch_meal(meal_id: int, body: MealPatch, user: CurrentUser, db: Session) -> MealOut:
    meal = await _get_owned(db, user, meal_id)
    for field in body.model_fields_set:
        value = getattr(body, field)
        if field in _REQUIRED and value is None:
            continue
        setattr(meal, field, _items(value) if field == "items" else value)
    await db.commit()
    return MealOut.model_validate(meal)


@router.delete("/{meal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meal(meal_id: int, user: CurrentUser, db: Session) -> None:
    await db.delete(await _get_owned(db, user, meal_id))
    await db.commit()
