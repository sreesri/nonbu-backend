from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.auth import CurrentUser, Session
from app.models import Fast, User, utcnow
from app.schemas import FastEnd, FastOut, FastPatch, FastStart
from app.timeutil import day_bounds, local_today

router = APIRouter(prefix="/fasts", tags=["fasts"])


async def _get_owned(session: Session, user: User, fast_id: int) -> Fast:
    fast = await session.get(Fast, fast_id)
    if fast is None or fast.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fast not found")
    return fast


def _validate_times(started_at: datetime, ended_at: datetime | None) -> None:
    now = utcnow() + timedelta(minutes=5)  # tolerate small clock skew
    if started_at > now or (ended_at is not None and ended_at > now):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "time is in the future")
    if ended_at is not None and ended_at <= started_at:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "ended_at must be after started_at"
        )


async def _commit_or_conflict(session: Session) -> None:
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "a fast is already in progress")


@router.get("/current", response_model=FastOut | None)
async def current_fast(user: CurrentUser, session: Session) -> Fast | None:
    return await session.scalar(
        select(Fast).where(Fast.user_id == user.id, Fast.ended_at.is_(None))
    )


@router.post("/start", response_model=FastOut, status_code=status.HTTP_201_CREATED)
async def start_fast(body: FastStart, user: CurrentUser, session: Session) -> Fast:
    if await current_fast(user, session) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "a fast is already in progress")
    started_at = body.started_at or utcnow()
    _validate_times(started_at, None)
    fast = Fast(
        user_id=user.id,
        started_at=started_at,
        target_hours=body.target_hours or user.goals.default_fast_hours,
        notes=body.notes,
    )
    session.add(fast)
    await _commit_or_conflict(session)
    return fast


@router.post("/{fast_id}/end", response_model=FastOut)
async def end_fast(
    fast_id: int, body: FastEnd, user: CurrentUser, session: Session
) -> Fast:
    fast = await _get_owned(session, user, fast_id)
    if fast.ended_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "fast already ended")
    ended_at = body.ended_at or utcnow()
    _validate_times(fast.started_at, ended_at)
    fast.ended_at = ended_at
    await session.commit()
    return fast


@router.get("", response_model=list[FastOut])
async def list_fasts(
    user: CurrentUser,
    session: Session,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
) -> list[Fast]:
    """Fasts overlapping the local-date range [from, to] (default: last 30 days)."""
    to = to or local_today(user.timezone)
    from_ = from_ or to - timedelta(days=29)
    if from_ > to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "from is after to")
    window_start, _ = day_bounds(from_, user.timezone)
    _, window_end = day_bounds(to, user.timezone)
    result = await session.scalars(
        select(Fast)
        .where(
            Fast.user_id == user.id,
            Fast.started_at < window_end,
            or_(Fast.ended_at.is_(None), Fast.ended_at > window_start),
        )
        .order_by(Fast.started_at.desc())
    )
    return list(result)


@router.patch("/{fast_id}", response_model=FastOut)
async def patch_fast(
    fast_id: int, body: FastPatch, user: CurrentUser, session: Session
) -> Fast:
    fast = await _get_owned(session, user, fast_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field in ("started_at", "target_hours") and value is None:
            continue
        setattr(fast, field, value)
    _validate_times(fast.started_at, fast.ended_at)
    await _commit_or_conflict(session)
    return fast


@router.delete("/{fast_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fast(fast_id: int, user: CurrentUser, session: Session) -> None:
    fast = await _get_owned(session, user, fast_id)
    await session.delete(fast)
    await session.commit()
