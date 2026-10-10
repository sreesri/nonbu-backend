from collections.abc import Iterable
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from app.auth import CurrentUser, Session
from app.models import MealLog, TimelineSession, User, utcnow
from app.routers.meals import meals_between
from app.schemas import DailySummary, GoalsOut, MealOut, Streak, Totals, sum_totals
from app.timeutil import day_bounds, local_date, local_today, overlap_hours

router = APIRouter(prefix="/summary", tags=["summary"])

MAX_RANGE_DAYS = 92


def _totals(meals: list[MealLog]) -> Totals:
    return sum_totals((MealOut.model_validate(m).totals, 1) for m in meals)


async def _summaries(session: Session, user: User, from_: date, to: date) -> list[DailySummary]:
    range_start, _ = day_bounds(from_, user.timezone)
    _, range_end = day_bounds(to, user.timezone)
    meals = await meals_between(session, user, range_start, range_end)
    fasts = list(
        await session.scalars(
            select(TimelineSession).where(
                TimelineSession.user_id == user.id,
                TimelineSession.kind == "fast",
                TimelineSession.started_at < range_end,
                or_(TimelineSession.ended_at.is_(None), TimelineSession.ended_at > range_start),
            )
        )
    )
    now = utcnow()
    goals = GoalsOut.model_validate(user.goals)

    summaries = []
    day = from_
    while day <= to:
        start, end = day_bounds(day, user.timezone)
        day_meals = [m for m in meals if start <= m.eaten_at < end]
        fasting = sum(overlap_hours(f.started_at, f.ended_at or now, start, end) for f in fasts)
        summaries.append(
            DailySummary(
                date=day,
                totals=_totals(day_meals),
                goals=goals,
                entry_count=len(day_meals),
                fasting_hours=round(fasting, 2),
            )
        )
        day += timedelta(days=1)
    return summaries


@router.get("/daily", response_model=DailySummary)
async def daily_summary(
    user: CurrentUser, session: Session, day: date | None = Query(None, alias="date")
) -> DailySummary:
    day = day or local_today(user.timezone)
    return (await _summaries(session, user, day, day))[0]


@router.get("/range", response_model=list[DailySummary])
async def range_summary(
    user: CurrentUser,
    session: Session,
    from_: date = Query(alias="from"),
    to: date = Query(),
) -> list[DailySummary]:
    if from_ > to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "from is after to")
    if (to - from_).days >= MAX_RANGE_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"range is limited to {MAX_RANGE_DAYS} days",
        )
    return await _summaries(session, user, from_, to)


def goal_days(fasts: Iterable[tuple[datetime, datetime, float]], tz_name: str) -> set[date]:
    """Local dates on which a fast (start, end, target hours) that reached its goal ended."""
    return {
        local_date(end, tz_name)
        for start, end, target_hours in fasts
        if end - start >= timedelta(hours=target_hours)
    }


def count_streak(days: set[date], today: date) -> int:
    """Consecutive days in `days` ending today, or yesterday while today is still open."""
    day = today if today in days else today - timedelta(days=1)
    count = 0
    while day in days:
        count += 1
        day -= timedelta(days=1)
    return count


@router.get("/streak", response_model=Streak)
async def streak(user: CurrentUser, session: Session) -> Streak:
    fasts = await session.execute(
        select(
            TimelineSession.started_at, TimelineSession.ended_at, TimelineSession.target_hours
        ).where(
            TimelineSession.user_id == user.id,
            TimelineSession.kind == "fast",
            TimelineSession.ended_at.is_not(None),
        )
    )
    completed = ((start, end, target) for start, end, target in fasts if end is not None)
    days = goal_days(completed, user.timezone)
    return Streak(days=count_streak(days, local_today(user.timezone)))
