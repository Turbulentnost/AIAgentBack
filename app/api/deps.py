from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User, UserSession
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login", auto_error=False)
DbSession = Annotated[AsyncSession, Depends(get_db)]


def _auth_disabled_allowed() -> bool:
    return settings.AUTH_DISABLED and settings.ENVIRONMENT in ("dev", "test")


def _dev_auto_login_allowed() -> bool:
    return settings.DEV_AUTO_LOGIN and settings.ENVIRONMENT in ("dev", "test")


async def _resolve_dev_bypass_user(db: AsyncSession) -> User:
    """Pick a real DB user for AUTH_DISABLED (FK-safe, superuser preferred)."""
    email = (settings.AUTH_DISABLED_USER_EMAIL or "").strip()
    if email:
        user = await db.scalar(
            select(User).where(User.email == email, User.is_active.is_(True), User.deleted_at.is_(None))
        )
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"AUTH_DISABLED: пользователь {email} не найден или неактивен",
            )
        return user

    user = await db.scalar(
        select(User)
        .where(User.is_superuser.is_(True), User.is_active.is_(True), User.deleted_at.is_(None))
        .order_by(User.created_at.asc())
        .limit(1)
    )
    if user is not None:
        return user

    user = await db.scalar(
        select(User)
        .where(User.is_active.is_(True), User.deleted_at.is_(None))
        .order_by(User.created_at.asc())
        .limit(1)
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AUTH_DISABLED: в БД нет активного пользователя для impersonation",
        )
    return user


async def authenticate_access_token(db: AsyncSession, token: str) -> User:
    exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не удалось проверить учётные данные")
    if not token:
        raise exc
    payload = decode_access_token(token)
    if payload is None or "sub" not in payload:
        raise exc
    token_id = payload.get("jti")
    if not token_id:
        raise exc
    session = await db.scalar(select(UserSession).where(UserSession.token_jti == token_id))
    if session is None or session.revoked_at is not None or session.expires_at <= datetime.now(timezone.utc):
        raise exc
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except ValueError as err:
        raise exc from err
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise exc
    return user


async def get_current_user(db: DbSession, token: Annotated[str | None, Depends(oauth2_scheme)]) -> User:
    if not token:
        if _auth_disabled_allowed():
            return await _resolve_dev_bypass_user(db)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не удалось проверить учётные данные")
    try:
        return await authenticate_access_token(db, token)
    except HTTPException:
        if _auth_disabled_allowed():
            return await _resolve_dev_bypass_user(db)
        raise


CurrentUser = Annotated[User, Depends(get_current_user)]

