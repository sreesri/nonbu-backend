from fastapi import APIRouter

from app.auth import CurrentUser, Session
from app.schemas import UserOut, UserPatch

router = APIRouter(prefix="/me", tags=["me"])


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
        goals = body.goals.model_dump(exclude_unset=True)
        # Calorie/macro goals may be cleared with null; the fast length may not.
        if "default_fast_hours" in goals and goals["default_fast_hours"] is None:
            del goals["default_fast_hours"]
        for field, value in goals.items():
            setattr(user.goals, field, value)
    await session.commit()
    return UserOut.model_validate(user)
