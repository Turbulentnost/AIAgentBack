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
async def get_current_user(db: DbSession, token: Annotated[str | None, Depends(oauth2_scheme)]) -> User:
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
CurrentUser = Annotated[User, Depends(get_current_user)]

