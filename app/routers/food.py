from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.auth import CurrentUser, Session
from app.models import FoodLog, User, utcnow
from app.schemas import FoodIn, FoodOut, FoodPatch
from app.timeutil import day_bounds, local_today

router = APIRouter(prefix="/food", tags=["food"])

# Fields that must not be cleared by a PATCH with null.
_REQUIRED = {"name", "meal_type", "eaten_at"}


async def _get_owned(session: Session, user: User, food_id: int) -> FoodLog:
    entry = await session.get(FoodLog, food_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "food entry not found")
    return entry


async def entries_between(
    session: Session, user: User, start: datetime, end: datetime
) -> list[FoodLog]:
    result = await session.scalars(
        select(FoodLog)
        .where(
            FoodLog.user_id == user.id,
            FoodLog.eaten_at >= start,
            FoodLog.eaten_at < end,
        )
        .order_by(FoodLog.eaten_at)
    )
    return list(result)


@router.get("", response_model=list[FoodOut])
async def list_food(
    user: CurrentUser, session: Session, day: date | None = Query(None, alias="date")
) -> list[FoodLog]:
    start, end = day_bounds(day or local_today(user.timezone), user.timezone)
    return await entries_between(session, user, start, end)


@router.get("/recent", response_model=list[FoodOut])
async def recent_food(
    user: CurrentUser, session: Session, limit: int = Query(20, ge=1, le=100)
) -> list[FoodLog]:
    """Most recent entry for each distinct food name, for quick re-adding."""
    result = await session.scalars(
        select(FoodLog)
        .where(FoodLog.user_id == user.id)
        .order_by(FoodLog.eaten_at.desc())
        .limit(500)
    )
    seen: set[str] = set()
    recent: list[FoodLog] = []
    for entry in result:
        key = entry.name.strip().lower()
        if key not in seen:
            seen.add(key)
            recent.append(entry)
            if len(recent) == limit:
                break
    return recent


@router.post("", response_model=FoodOut, status_code=status.HTTP_201_CREATED)
async def create_food(body: FoodIn, user: CurrentUser, session: Session) -> FoodLog:
    data = body.model_dump()
    data["eaten_at"] = data["eaten_at"] or utcnow()
    entry = FoodLog(user_id=user.id, **data)
    session.add(entry)
    await session.commit()
    return entry


@router.get("/{food_id}", response_model=FoodOut)
async def get_food(food_id: int, user: CurrentUser, session: Session) -> FoodLog:
    return await _get_owned(session, user, food_id)


@router.patch("/{food_id}", response_model=FoodOut)
async def patch_food(food_id: int, body: FoodPatch, user: CurrentUser, session: Session) -> FoodLog:
    entry = await _get_owned(session, user, food_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field in _REQUIRED and value is None:
            continue
        setattr(entry, field, value)
    await session.commit()
    return entry


@router.delete("/{food_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_food(food_id: int, user: CurrentUser, session: Session) -> None:
    entry = await _get_owned(session, user, food_id)
    await session.delete(entry)
    await session.commit()
