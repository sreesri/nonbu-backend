from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from google.auth.exceptions import TransportError
from sqlalchemy import select

from app import auth
from app.auth import Session
from app.config import Settings, get_settings
from app.models import RefreshToken, User, UserGoals, utcnow
from app.schemas import GoogleLoginIn, RefreshIn, TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def _issue_tokens(
    session: Session, user: User, settings: Settings, replaces: RefreshToken | None = None
) -> TokenPair:
    raw, row = auth.new_refresh_token(user.id, settings)
    session.add(row)
    if replaces is not None:
        await session.flush()
        replaces.replaced_by_id = row.id
    await session.commit()
    return TokenPair(
        access_token=auth.create_access_token(user.id, settings),
        refresh_token=raw,
        expires_in=settings.access_token_minutes * 60,
    )


@router.post("/google", response_model=TokenPair)
async def google_login(body: GoogleLoginIn, session: Session, settings: SettingsDep) -> TokenPair:
    try:
        # Verification may fetch Google's certs over the network; keep it off the loop.
        claims = await run_in_threadpool(auth.verify_google_id_token, body.id_token, settings)
    except TransportError as err:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "could not reach Google to verify token"
        ) from err
    except ValueError as err:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid Google token") from err

    email = str(claims.get("email", "")).lower()
    if not claims.get("email_verified") or email not in settings.allowed_emails:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account not allowed")

    user = await session.scalar(select(User).where(User.google_sub == claims["sub"]))
    if user is None:
        user = User(google_sub=claims["sub"], email=email, goals=UserGoals())
        session.add(user)
    user.email = email
    user.name = claims.get("name") or user.name
    user.avatar_url = claims.get("picture") or user.avatar_url
    await session.flush()
    return await _issue_tokens(session, user, settings)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshIn, session: Session, settings: SettingsDep) -> TokenPair:
    row = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == auth.hash_refresh_token(body.refresh_token)
        )
    )
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
    now = utcnow()
    if row is None or row.expires_at <= now:
        raise invalid
    user = await session.get(User, row.user_id)
    if user is None or user.email not in settings.allowed_emails:
        raise invalid
    if row.revoked_at is None:
        # Rotate: each refresh token is single-use.
        row.revoked_at = now
    else:
        # Already rotated. If the token it was rotated into has never been used, the app never
        # received it (the response was lost to a dropped connection or a killed background task),
        # so replace that one instead of signing the user out. Once the newer token has been
        # used, or the token was logged out, reuse is refused.
        successor = (
            await session.get(RefreshToken, row.replaced_by_id) if row.replaced_by_id else None
        )
        if successor is None or successor.revoked_at is not None:
            raise invalid
        successor.revoked_at = now
    return await _issue_tokens(session, user, settings, replaces=row)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshIn, session: Session) -> None:
    row = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == auth.hash_refresh_token(body.refresh_token)
        )
    )
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
        await session.commit()
