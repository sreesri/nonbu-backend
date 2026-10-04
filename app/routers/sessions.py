from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.auth import CurrentUser, Session
from app.models import TimelineSession, User, utcnow
from app.schemas import GoalsOut, SessionKind, SessionOut, SessionPatch, SessionSwitch
from app.timeutil import day_bounds, local_today

router = APIRouter(prefix="/sessions", tags=["sessions"])

CLOCK_SKEW = timedelta(minutes=5)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


def default_target_hours(user: User, kind: SessionKind) -> float:
    """A session's default length from the user's fasting schedule, e.g. 16:8 -> 16h / 8h."""
    goals = GoalsOut.model_validate(user.goals)
    return goals.default_fast_hours if kind == "fast" else goals.eating_window_hours


async def _get_owned(db: Session, user: User, session_id: int) -> TimelineSession:
    row = await db.get(TimelineSession, session_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return row


def _validate_times(started_at: datetime, ended_at: datetime | None) -> None:
    latest_allowed = utcnow() + CLOCK_SKEW
    if started_at > latest_allowed or (ended_at is not None and ended_at > latest_allowed):
        raise _unprocessable("time is in the future")
    if ended_at is not None and ended_at <= started_at:
        raise _unprocessable("ended_at must be after started_at")


async def _commit_or_conflict(db: Session) -> None:
    try:
        await db.commit()
    except IntegrityError as err:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "a session is already in progress") from err


async def current_session(db: Session, user: User) -> TimelineSession | None:
    return await db.scalar(
        select(TimelineSession).where(
            TimelineSession.user_id == user.id, TimelineSession.ended_at.is_(None)
        )
    )


async def _neighbours(
    db: Session, user: User, row: TimelineSession
) -> tuple[TimelineSession | None, TimelineSession | None]:
    """The sessions immediately before and after `row` on the user's timeline."""
    others = select(TimelineSession).where(
        TimelineSession.user_id == user.id, TimelineSession.id != row.id
    )
    prev = await db.scalar(
        others.where(TimelineSession.started_at < row.started_at)
        .order_by(TimelineSession.started_at.desc())
        .limit(1)
    )
    nxt = await db.scalar(
        others.where(TimelineSession.started_at > row.started_at)
        .order_by(TimelineSession.started_at)
        .limit(1)
    )
    return prev, nxt


async def start_session(
    db: Session,
    user: User,
    kind: SessionKind,
    at: datetime,
    target_hours: float | None = None,
    notes: str | None = None,
) -> TimelineSession:
    """Close the open session (if any) at `at` and open a `kind` session from `at`; commits."""
    _validate_times(at, None)
    current = await current_session(db, user)
    if current is not None:
        if current.kind == kind:
            raise HTTPException(status.HTTP_409_CONFLICT, f"a {kind} session is in progress")
        if at <= current.started_at:
            raise _unprocessable("must be after the current session started")
        current.ended_at = at
        # Close it before inserting, or the one-open-session index would reject the insert.
        await db.flush()
    else:
        last_end = await db.scalar(
            select(TimelineSession.ended_at)
            .where(TimelineSession.user_id == user.id)
            .order_by(TimelineSession.started_at.desc())
            .limit(1)
        )
        if last_end is not None and at < last_end:
            raise _unprocessable("overlaps the previous session")
    row = TimelineSession(
        user_id=user.id,
        kind=kind,
        started_at=at,
        target_hours=target_hours or default_target_hours(user, kind),
        notes=notes,
    )
    db.add(row)
    await _commit_or_conflict(db)
    return row


@router.get("/current", response_model=SessionOut | None)
async def get_current(user: CurrentUser, db: Session) -> SessionOut | None:
    row = await current_session(db, user)
    return None if row is None else SessionOut.model_validate(row)


@router.post("/switch", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def switch_session(body: SessionSwitch, user: CurrentUser, db: Session) -> SessionOut:
    row = await start_session(
        db, user, body.kind, body.at or utcnow(), body.target_hours, body.notes
    )
    return SessionOut.model_validate(row)


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    user: CurrentUser,
    db: Session,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    kind: SessionKind | None = None,
) -> list[SessionOut]:
    """Sessions overlapping the local-date range [from, to] (default: last 30 days)."""
    to = to or local_today(user.timezone)
    from_ = from_ or to - timedelta(days=29)
    if from_ > to:
        raise _unprocessable("from is after to")
    window_start, _ = day_bounds(from_, user.timezone)
    _, window_end = day_bounds(to, user.timezone)
    query = select(TimelineSession).where(
        TimelineSession.user_id == user.id,
        TimelineSession.started_at < window_end,
        or_(TimelineSession.ended_at.is_(None), TimelineSession.ended_at > window_start),
    )
    if kind is not None:
        query = query.where(TimelineSession.kind == kind)
    rows = await db.scalars(query.order_by(TimelineSession.started_at.desc()))
    return [SessionOut.model_validate(r) for r in rows]


@router.patch("/{session_id}", response_model=SessionOut)
async def patch_session(
    session_id: int, body: SessionPatch, user: CurrentUser, db: Session
) -> SessionOut:
    """Edit a session. A boundary shared with a neighbour moves the neighbour's edge too,
    so the timeline stays contiguous; a change that would overlap or empty one is rejected."""
    row = await _get_owned(db, user, session_id)
    prev, nxt = await _neighbours(db, user, row)
    old_start, old_end = row.started_at, row.ended_at

    for field, value in body.model_dump(exclude_unset=True).items():
        if field in ("started_at", "target_hours") and value is None:
            continue
        setattr(row, field, value)
    _validate_times(row.started_at, row.ended_at)

    if prev is not None and prev.ended_at is not None:
        if prev.ended_at == old_start:
            prev.ended_at = row.started_at
            if prev.ended_at <= prev.started_at:
                raise _unprocessable("would leave the previous session empty")
        elif row.started_at < prev.ended_at:
            raise _unprocessable("overlaps the previous session")

    if nxt is not None:
        if row.ended_at is None:
            raise _unprocessable("only the latest session can be in progress")
        if nxt.started_at == old_end:
            nxt.started_at = row.ended_at
            if nxt.started_at >= (nxt.ended_at or utcnow()):
                raise _unprocessable("would leave the next session empty")
        elif row.ended_at > nxt.started_at:
            raise _unprocessable("overlaps the next session")

    await _commit_or_conflict(db)
    return SessionOut.model_validate(row)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: int, user: CurrentUser, db: Session) -> None:
    """Delete a session, closing the gap it leaves: deleting the open session resumes the
    previous one (undoing a switch), and deleting one between two sessions of the same kind
    merges them."""
    row = await _get_owned(db, user, session_id)
    prev, nxt = await _neighbours(db, user, row)
    joined = prev if prev is not None and prev.ended_at == row.started_at else None
    absorbed = (
        nxt
        if joined is not None
        and nxt is not None
        and nxt.kind == joined.kind
        and nxt.started_at == row.ended_at
        else None
    )
    if absorbed is not None:
        reopen_until: datetime | None = absorbed.ended_at
    elif row.ended_at is None:
        reopen_until = None
    else:
        joined = None  # a gap is left; nothing to stretch over it

    await db.delete(row)
    if absorbed is not None:
        await db.delete(absorbed)
    # Remove them first: re-opening `joined` must not collide with the open-session index.
    await db.flush()
    if joined is not None:
        joined.ended_at = reopen_until
    await _commit_or_conflict(db)
