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


async def authenticate_access_token(
    db: AsyncSession,
    token: str,
    *,
    allow_without_session: bool | None = None,
) -> User:
    exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не удалось проверить учётные данные")
    if not token:
        raise exc
    payload = decode_access_token(token)
    if payload is None or "sub" not in payload:
        raise exc
    token_id = payload.get("jti")
    if not token_id:
        raise exc
    skip_session = (
        settings.AUTH_ALLOW_JWT_WITHOUT_SESSION
        if allow_without_session is None
        else allow_without_session
    )
    session = await db.scalar(select(UserSession).where(UserSession.token_jti == token_id))
    if session is None or session.revoked_at is not None or session.expires_at <= datetime.now(timezone.utc):
        if not skip_session:
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не удалось проверить учётные данные")
    return await authenticate_access_token(db, token)


async def get_document_analysis_user(
    db: DbSession,
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User | None:
    """Auth для Excel API Авиона при раздельных backend (login host ≠ aveon host).

    При DOCUMENT_ANALYSIS_REQUIRE_AUTH=false эндпоинты доступны без сессии платформы
    (токен с другого host всё равно принимается best-effort и игнорируется при ошибке).
    """
    if not settings.DOCUMENT_ANALYSIS_REQUIRE_AUTH:
        if not token:
            return None
        try:
            return await authenticate_access_token(
                db,
                token,
                allow_without_session=True,
            )
        except HTTPException:
            return None
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не удалось проверить учётные данные")
    return await authenticate_access_token(
        db,
        token,
        allow_without_session=settings.AUTH_ALLOW_JWT_WITHOUT_SESSION,
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
DocumentAnalysisUser = Annotated[User | None, Depends(get_document_analysis_user)]
