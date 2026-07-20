from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.user import AdminUserCreate, UserRead
from app.services.audit_service import AuditService
from app.services.profile_image_service import ProfileImageService
from app.services.user_service import UserService

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


@router.get("", response_model=list[UserRead])
async def list_admin_users(
    db: DbSession,
    current_user: CurrentUser,
    limit: int = 50,
    offset: int = 0,
):
    _require_admin(current_user)
    users = await UserService(db).list(limit, offset)
    return [await _user_read(db, user) for user in users]


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_admin_user(
    db: DbSession,
    current_user: CurrentUser,
    data: AdminUserCreate,
):
    _require_admin(current_user)
    try:
        user = await UserService(db).create_by_admin(data, created_by=current_user.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await AuditService(db).log(
        action="admin.users.create",
        actor_id=current_user.id,
        resource_type="user",
        resource_id=str(user.id),
        payload={
            "email": user.email,
            "department_id": str(user.department_id) if user.department_id else None,
            "role_id": str(user.role_id) if user.role_id else None,
            "agent_access_count": len(data.agent_access),
            "must_change_password": user.must_change_password,
        },
    )
    return await _user_read(db, user)


@router.post("/{user_id}/deactivate", response_model=UserRead)
async def deactivate_admin_user(db: DbSession, current_user: CurrentUser, user_id: uuid.UUID):
    _require_admin(current_user)
    service = UserService(db)
    user = await service.get(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    await service.deactivate(user)
    await AuditService(db).log(
        action="admin.users.deactivate",
        actor_id=current_user.id,
        resource_type="user",
        resource_id=str(user.id),
    )
    return await _user_read(db, user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin_user(
    db: DbSession,
    current_user: CurrentUser,
    user_id: uuid.UUID,
) -> Response:
    _require_admin(current_user)
    if user_id == current_user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Нельзя удалить собственную учётную запись",
        )

    service = UserService(db)
    user = await service.get(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")

    await service.soft_delete(user)
    await AuditService(db).log(
        action="admin.users.delete",
        actor_id=current_user.id,
        resource_type="user",
        resource_id=str(user.id),
        payload={"email": user.email, "soft_delete": True},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _require_admin(user) -> None:
    if not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")


async def _user_read(db: DbSession, user) -> UserRead:
    await db.refresh(user)
    data = UserRead.model_validate(user).model_dump()
    data["avatar_url"] = ProfileImageService(db).build_avatar_url(user)
    return UserRead(**data)
