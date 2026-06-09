from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.department import DepartmentCreate, DepartmentRead, DepartmentSyncResult, DepartmentSyncStatus, DepartmentUpdate
from app.services.audit_service import AuditService
from app.services.department_sync_service import DepartmentSyncCooldownError, DepartmentSyncService
from app.services.user_service import DepartmentService

router = APIRouter(prefix="/departments", tags=["departments"])


@router.get("", response_model=list[DepartmentRead])
async def list_departments(
    db: DbSession,
    current_user: CurrentUser,
    limit: int = 1000,
    offset: int = 0,
    active_only: bool = True,
):
    return await DepartmentService(db).list(limit, offset, active_only=active_only)


@router.get("/sync/status", response_model=DepartmentSyncStatus)
async def department_sync_status(db: DbSession, current_user: CurrentUser):
    return await DepartmentSyncService(db).status()


@router.post("/sync", response_model=DepartmentSyncResult)
async def sync_departments_from_1c(db: DbSession, current_user: CurrentUser):
    _require_admin(current_user)
    service = DepartmentSyncService(db)
    try:
        result = await service.sync_from_1c()
        await AuditService(db).log(
            action="departments.sync_1c",
            actor_id=current_user.id,
            resource_type="department",
            payload=result,
        )
        await db.commit()
        state = await service.status()
        counts = {key: result.get(key, 0) for key in ("created_count", "updated_count", "deactivated_count", "synced_count")}
        return DepartmentSyncResult(
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
    except DepartmentSyncCooldownError as exc:
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
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Не удалось обновить подразделения из 1С: {exc}") from exc


@router.post("", response_model=DepartmentRead, status_code=status.HTTP_201_CREATED)
async def create_department(db: DbSession, current_user: CurrentUser, data: DepartmentCreate):
    _require_admin(current_user)
    department = await DepartmentService(db).create(data)
    await AuditService(db).log(
        action="departments.create",
        actor_id=current_user.id,
        resource_type="department",
        resource_id=str(department.id),
    )
    return department


@router.patch("/{department_id}", response_model=DepartmentRead)
async def update_department(
    db: DbSession,
    current_user: CurrentUser,
    department_id: uuid.UUID,
    data: DepartmentUpdate,
):
    _require_admin(current_user)
    service = DepartmentService(db)
    department = await service.get(department_id)
    if department is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Подразделение не найдено")
    updated = await service.update(department, data)
    await AuditService(db).log(
        action="departments.update",
        actor_id=current_user.id,
        resource_type="department",
        resource_id=str(department.id),
        payload=data.model_dump(exclude_unset=True),
    )
    return updated


def _require_admin(user) -> None:
    if not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
