from fastapi import APIRouter, HTTPException, status

from app.auth import CurrentUser, Session
from app.models import User, utcnow
from app.routers.sessions import default_target_hours, start_session
from app.schemas import MAX_SESSION_HOURS, GoalsIn, OnboardingIn, UserOut, UserPatch
from app.timeutil import day_bounds, local_today

router = APIRouter(prefix="/me", tags=["me"])


def _apply_goals(user: User, body: GoalsIn) -> None:
    goals = body.model_dump(exclude_unset=True)
    # Calorie/macro goals may be cleared with null; the fast length may not.
    if "default_fast_hours" in goals and goals["default_fast_hours"] is None:
        del goals["default_fast_hours"]
    for field, value in goals.items():
        setattr(user.goals, field, value)


@router.get("", response_model=UserOut)
async def get_me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("", response_model=UserOut)
async def patch_me(body: UserPatch, user: CurrentUser, session: Session) -> UserOut:
    if body.name is not None:
        user.name = body.name
    if body.timezone is not None:
        user.timezone = body.timezone
    if body.goals is not None:
        _apply_goals(user, body.goals)
    await session.commit()
    return UserOut.model_validate(user)


@router.post("/onboarding", response_model=UserOut)
async def complete_onboarding(body: OnboardingIn, user: CurrentUser, session: Session) -> UserOut:
    """First-run setup: save the schedule/goals and open the session the user is in now."""
    if user.onboarded_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "already onboarded")
    user.timezone = body.timezone
    _apply_goals(user, body.goals)

    current = body.current
    now = utcnow()
    if current.fast_at is not None:
        if current.fast_at <= now:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "fast_at is in the past")
        started_at, _ = day_bounds(local_today(user.timezone), user.timezone)
        target_hours = (current.fast_at - started_at).total_seconds() / 3600
        if target_hours > MAX_SESSION_HOURS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "fast_at is too far in the future"
            )
    elif current.started_at is not None:
        started_at = current.started_at
        target_hours = default_target_hours(user, current.kind)

    user.onboarded_at = now
    # Commits the goals, timezone and onboarding flag together with the new session.
    await start_session(session, user, current.kind, started_at, target_hours)
    return UserOut.model_validate(user)
