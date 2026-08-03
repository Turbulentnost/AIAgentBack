from __future__ import annotations
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    CurrentUser,
    DbSession,
    _dev_auto_login_allowed,
    _resolve_dev_bypass_user,
    oauth2_scheme,
)
from app.core.security import create_access_token, decode_access_token
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    OneCLoginCompleteRequest,
    OneCLoginRequest,
    OneCLoginResponse,
    OneCSessionRead,
    Token,
)
from app.schemas.user import UserRead
from app.services.auth_service import AuthService, PasswordChangeRequired
from app.services.onec_auth_service import OneCAuthService, OneCSessionExpiredError, OneCSessionNotFoundError
from app.services.profile_image_service import ProfileImageService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/dev-auto-login", response_model=Token)
async def dev_auto_login(db: DbSession, request: Request) -> Token:
    """Issue a real JWT for the local bypass user (dev/test only)."""
    if not _dev_auto_login_allowed():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dev auto-login выключен (DEV_AUTO_LOGIN + ENVIRONMENT=dev|test)",
        )
    user = await _resolve_dev_bypass_user(db)
    return await AuthService(db).issue_session(
        user,
        action="auth.dev_auto_login",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/login", response_model=Token)
async def login(db: DbSession, credentials: LoginRequest, request: Request) -> Token:
    try:
        user, token = await AuthService(db).authenticate(
            email=credentials.email,
            password=credentials.password,
            new_password=credentials.new_password,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except PasswordChangeRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={"code": "password_change_required", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль")
    return token


@router.post("/onec/login", response_model=OneCLoginResponse)
async def login_with_onec(
    db: DbSession,
    payload: OneCLoginRequest,
    request: Request,
) -> OneCLoginResponse:
    try:
        user, token, onec_session, _created = await OneCAuthService(db).login(
            fio=payload.fio,
            password=payload.password,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Пользователь уже создан. Повторите вход через 1С.",
        ) from exc
    return await _onec_login_response(db, user, token, onec_session)


@router.post("/onec/session", response_model=OneCLoginResponse)
async def complete_onec_login(
    db: DbSession,
    payload: OneCLoginCompleteRequest,
    request: Request,
) -> OneCLoginResponse:
    try:
        user, token, _created = await OneCAuthService(db).complete_login(
            fio=payload.fio,
            password=payload.password,
            token=payload.token,
            expires_at=payload.expires_at,
            resolved_user=payload.resolved_user,
            resolved_user_source=payload.resolved_user_source,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    onec_service = OneCAuthService(db)
    onec_session = onec_service.get_session_for_user(user)
    return await _onec_login_response(db, user, token, onec_session)


@router.get("/onec/session", response_model=OneCSessionRead)
async def get_onec_session(db: DbSession, current_user: CurrentUser) -> OneCSessionRead:
    try:
        session = OneCAuthService(db).get_session_for_user(current_user)
    except OneCSessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except OneCSessionExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "onec_session_expired", "message": str(exc)},
        ) from exc
    return _onec_session_read(session)


@router.delete("/onec/session", status_code=status.HTTP_204_NO_CONTENT)
async def clear_onec_session(db: DbSession, current_user: CurrentUser) -> Response:
    OneCAuthService(db).clear_session(current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/refresh", response_model=Token)
async def refresh(current_user: CurrentUser) -> Token:
    return Token(access_token=create_access_token(current_user.id))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    db: DbSession,
    current_user: CurrentUser,
    request: Request,
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> Response:
    payload = decode_access_token(token) if token else None
    await AuthService(db).logout(
        user=current_user,
        token_id=payload.get("jti") if payload else None,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserRead)
async def current_user(db: DbSession, current_user: CurrentUser) -> UserRead:
    return await _user_read(db, current_user)


async def _user_read(db: DbSession, user: User) -> UserRead:
    await db.refresh(user)
    data = UserRead.model_validate(user).model_dump()
    data["avatar_url"] = ProfileImageService(db).build_avatar_url(user)
    data["has_onec_credentials"] = user.onec_hashed_password is not None
    data["has_onec_session"] = user.onec_access_token is not None
    return UserRead(**data)


def _onec_session_read(session) -> OneCSessionRead:
    return OneCSessionRead(
        token=session.token,
        fio=session.fio,
        expires_at=session.expires_at,
        resolved_user=session.resolved_user,
        resolved_user_source=session.resolved_user_source,
        token_created_at=session.token_created_at,
        reused=session.reused,
    )


async def _onec_login_response(db: DbSession, user: User, token: Token, onec_session) -> OneCLoginResponse:
    user_read = await _user_read(db, user)
    return OneCLoginResponse(
        access_token=token.access_token,
        expires_at=token.expires_at,
        user=user_read,
        is_created_via_1c=user.is_created_via_1c,
        onec_session=_onec_session_read(onec_session),
        token_reused=onec_session.reused,
    )
