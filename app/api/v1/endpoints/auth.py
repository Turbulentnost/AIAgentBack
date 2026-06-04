from __future__ import annotations
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.deps import CurrentUser, DbSession, oauth2_scheme
from app.core.security import create_access_token, decode_access_token
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, Token
from app.schemas.user import UserRead
from app.services.auth_service import AuthService
from app.services.profile_image_service import ProfileImageService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(db: DbSession, data: RegisterRequest) -> UserRead:
    try:
        user = await AuthService(db).register(data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return await _user_read(db, user)


@router.post("/login", response_model=Token)
async def login(db: DbSession, credentials: LoginRequest, request: Request) -> Token:
    _, token = await AuthService(db).authenticate(
        email=credentials.email,
        password=credentials.password,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль")
    return token


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
    data = UserRead.model_validate(user).model_dump()
    data["avatar_url"] = ProfileImageService(db).build_avatar_url(user)
    return UserRead(**data)
