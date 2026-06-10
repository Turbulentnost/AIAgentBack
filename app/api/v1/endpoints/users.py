from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.api.deps import CurrentUser, DbSession
from app.integrations.minio import MinioObjectError
from app.schemas.user import EmployeeSyncResult, EmployeeSyncStatus, ResponsibleUserRead, UserRead, UserUpdate
from app.services.audit_service import AuditService
from app.services.employee_sync_service import EmployeeSyncCooldownError, EmployeeSyncService
from app.services.profile_image_service import AvatarMetadataSaveError, AvatarValidationError, ProfileImageService
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])

SELF_PROFILE_FIELDS = {
    "email",
    "username",
    "last_name",
    "first_name",
    "middle_name",
    "full_name",
    "phone",
    "position",
}


@router.get("", response_model=list[UserRead])
async def list_users(db: DbSession, current_user: CurrentUser, limit: int = 50, offset: int = 0):
    _require_admin(current_user)
    users = await UserService(db).list(limit, offset)
    return [await _user_read(db, user) for user in users]


@router.get("/responsible-candidates", response_model=list[ResponsibleUserRead])
async def list_responsible_candidates(db: DbSession, current_user: CurrentUser):
    _ = current_user
    users = await UserService(db).list_platform_access_users()
    return [
        ResponsibleUserRead(
            id=user.id,
            full_name=user.full_name,
            position=user.position,
            department_id=user.department_id,
            department_name=user.department.name if user.department else None,
        )
        for user in users
    ]


@router.get("/sync/status", response_model=EmployeeSyncStatus)
async def employee_sync_status(db: DbSession, current_user: CurrentUser):
    _require_admin(current_user)
    return await EmployeeSyncService(db).status()


@router.post("/sync", response_model=EmployeeSyncResult)
async def sync_employees_from_1c(db: DbSession, current_user: CurrentUser):
    _require_admin(current_user)
    service = EmployeeSyncService(db)
    try:
        result = await service.sync_from_1c()
        await AuditService(db).log(
            action="users.sync_1c",
            actor_id=current_user.id,
            resource_type="user",
            payload=result,
        )
        await db.commit()
        state = await service.status()
        counts = {
            key: result.get(key, 0)
            for key in (
                "created_count",
                "updated_count",
                "deactivated_count",
                "skipped_count",
                "missing_department_count",
                "synced_count",
            )
        }
        return EmployeeSyncResult(
            key=state.key,
            source_system=state.source_system,
            resource=state.resource,
            last_synced_at=state.last_synced_at,
            next_allowed_at=state.next_allowed_at,
            status=state.status,
            items_count=state.items_count,
            error_message=state.error_message,
            payload=state.payload,
            **counts,
        )
    except EmployeeSyncCooldownError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": str(exc),
                "next_allowed_at": exc.next_allowed_at.isoformat(),
            },
        ) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Не удалось обновить сотрудников из 1С: {exc}") from exc


@router.get("/{user_id}", response_model=UserRead)
async def get_user(db: DbSession, current_user: CurrentUser, user_id: uuid.UUID):
    if not current_user.is_superuser and current_user.id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    user = await UserService(db).get(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    return await _user_read(db, user)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    db: DbSession,
    current_user: CurrentUser,
    user_id: uuid.UUID,
    data: UserUpdate,
):
    if not current_user.is_superuser and current_user.id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    service = UserService(db)
    user = await service.get(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")

    update_data = data
    if not current_user.is_superuser:
        update_data = UserUpdate(
            **{
                key: value
                for key, value in data.model_dump(exclude_unset=True).items()
                if key in SELF_PROFILE_FIELDS
            }
        )

    updated = await service.update(user, update_data)
    await AuditService(db).log(
        action="users.update",
        actor_id=current_user.id,
        resource_type="user",
        resource_id=str(user.id),
        payload=update_data.model_dump(exclude_unset=True),
    )
    return await _user_read(db, updated)


@router.post("/{user_id}/deactivate", response_model=UserRead)
async def deactivate_user(db: DbSession, current_user: CurrentUser, user_id: uuid.UUID):
    _require_admin(current_user)
    service = UserService(db)
    user = await service.get(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    await service.deactivate(user)
    await AuditService(db).log(
        action="users.deactivate",
        actor_id=current_user.id,
        resource_type="user",
        resource_id=str(user.id),
    )
    return await _user_read(db, user)


@router.post("/{user_id}/avatar", response_model=UserRead)
async def upload_avatar(
    db: DbSession,
    current_user: CurrentUser,
    user_id: uuid.UUID,
    file: Annotated[UploadFile, File(...)],
):
    if not current_user.is_superuser and current_user.id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    user = await UserService(db).get(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    uploaded_object_name: str | None = None
    try:
        updated = await ProfileImageService(db).upload_avatar(user, file)
        uploaded_object_name = updated.avatar_object_name
        await AuditService(db).log(
            action="users.avatar_upload",
            actor_id=current_user.id,
            resource_type="user",
            resource_id=str(user.id),
        )
        await db.commit()
    except AvatarValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except MinioObjectError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except AvatarMetadataSaveError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        if uploaded_object_name:
            try:
                ProfileImageService(db).storage.delete(uploaded_object_name)
            except MinioObjectError as cleanup_exc:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "Аватар загружен в MinIO, но транзакция PostgreSQL не завершилась. "
                    "Автоматически удалить объект не удалось; нужна ручная очистка.",
                ) from cleanup_exc
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Аватар загружен в MinIO, но транзакция PostgreSQL не завершилась. Объект удалён из MinIO.",
        ) from exc
    return await _user_read(db, updated)


def _require_admin(user) -> None:
    if not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")


async def _user_read(db: DbSession, user) -> UserRead:
    await db.refresh(user)
    data = UserRead.model_validate(user).model_dump()
    data["avatar_url"] = ProfileImageService(db).build_avatar_url(user)
    return UserRead(**data)
