from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.api.deps import CurrentUser, DbSession
from app.integrations.minio import MinioObjectError
from app.schemas.agent import (
    AgentAccessManagementRead,
    AgentAccessRead,
    AgentAccessUpdate,
    AgentCreate,
    AgentDepartmentGrantRead,
    AgentRead,
    AgentUpdate,
    AgentUserGrantRead,
)
from app.services.agent_access_service import AgentAccessService, AgentAccessServiceError
from app.services.agent_icon_service import AgentIconService
from app.services.agent_service import AgentService
from app.services.audit_service import AuditService
from app.services.meeting_permission import append_meeting_agent_for_office_management
from app.services.nd_control_permission import append_nd_control_agent_for_quality_deputy
from app.services.permission_service import PermissionService
from app.services.cfo_head_permission import append_cfo_head_agent
from app.services.finance_director_permission import append_finance_director_agent
from app.services.executive_director_permission import append_executive_director_agent
from app.services.chief_accountant_permission import append_chief_accountant_agent
from app.services.procurement_permission import (
    append_production_preparation_engineer_agent,
)
from app.services.profile_image_service import AvatarValidationError

router = APIRouter(prefix="/agents", tags=["agents"])


async def _agent_read(db: DbSession, agent) -> AgentRead:
    data = AgentRead.model_validate(agent).model_dump()
    data["icon_url"] = AgentIconService(db).build_icon_url(agent)
    return AgentRead(**data)


async def _agent_access_read(db: DbSession, agent, current_user) -> AgentAccessRead:
    data = (await _agent_read(db, agent)).model_dump()
    data.update(
        {
            "access_level": "full" if current_user.is_superuser else "granted",
            "can_run": True,
            "can_view_results": True,
            "can_approve": current_user.is_superuser,
            "can_configure": current_user.is_superuser,
        }
    )
    return AgentAccessRead(**data)


@router.get("/available", response_model=list[AgentAccessRead])
async def list_available_agents(db: DbSession, current_user: CurrentUser):
    agents = await PermissionService(db).list_available_agents(current_user)
    agents = await append_nd_control_agent_for_quality_deputy(db, current_user, agents)
    agents = await append_meeting_agent_for_office_management(db, current_user, agents)
    agents = await append_production_preparation_engineer_agent(db, current_user, agents)
    agents = await append_cfo_head_agent(db, current_user, agents)
    agents = await append_finance_director_agent(db, current_user, agents)
    agents = await append_executive_director_agent(db, current_user, agents)
    agents = await append_chief_accountant_agent(db, current_user, agents)
    return [await _agent_access_read(db, agent, current_user) for agent in agents]


@router.get("", response_model=list[AgentRead])
async def list_agents(db: DbSession, limit: int = 50, offset: int = 0):
    agents = await AgentService(db).list(limit, offset)
    return [await _agent_read(db, agent) for agent in agents]


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(db: DbSession, data: AgentCreate):
    agent = await AgentService(db).create(data)
    return await _agent_read(db, agent)


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(db: DbSession, agent_id: uuid.UUID):
    agent = await AgentService(db).get(agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Агент не найден")
    return await _agent_read(db, agent)


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(db: DbSession, agent_id: uuid.UUID, data: AgentUpdate):
    service = AgentService(db)
    agent = await service.get(agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Агент не найден")
    updated = await service.update(agent, data)
    return await _agent_read(db, updated)


@router.get("/{agent_id}/access", response_model=AgentAccessManagementRead)
async def list_agent_access(agent_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    if not current_user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    try:
        department_grants, user_grants = await AgentAccessService(db).list_access(agent_id)
        return AgentAccessManagementRead(
            department_grants=[AgentDepartmentGrantRead.model_validate(item) for item in department_grants],
            user_grants=[AgentUserGrantRead.model_validate(item) for item in user_grants],
        )
    except AgentAccessServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.put("/{agent_id}/access", response_model=AgentAccessManagementRead)
async def replace_agent_access(
    agent_id: uuid.UUID,
    payload: AgentAccessUpdate,
    db: DbSession,
    current_user: CurrentUser,
):
    if not current_user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    try:
        department_grants, user_grants = await AgentAccessService(db).replace_access(
            agent_id,
            payload,
            current_user=current_user,
        )
        await AuditService(db).log(
            action="agents.access_replaced",
            actor_id=current_user.id,
            resource_type="agent",
            resource_id=str(agent_id),
            payload={
                "department_grants": len(department_grants),
                "user_grants": len(user_grants),
            },
        )
        await db.commit()
        return AgentAccessManagementRead(
            department_grants=[AgentDepartmentGrantRead.model_validate(item) for item in department_grants],
            user_grants=[AgentUserGrantRead.model_validate(item) for item in user_grants],
        )
    except AgentAccessServiceError as exc:
        await db.rollback()
        message = str(exc)
        status_code = status.HTTP_403_FORBIDDEN if "администратор" in message else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code, message) from exc


@router.post("/{agent_id}/icon", response_model=AgentRead)
async def upload_agent_icon(
    db: DbSession,
    current_user: CurrentUser,
    agent_id: uuid.UUID,
    file: Annotated[UploadFile, File(...)],
):
    if not current_user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")

    agent = await AgentService(db).get(agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Агент не найден")

    uploaded_object_name: str | None = None
    try:
        updated = await AgentIconService(db).upload_icon(agent, file)
        uploaded_object_name = updated.icon_object_name
        await AuditService(db).log(
            action="agents.icon_upload",
            actor_id=current_user.id,
            resource_type="agent",
            resource_id=str(agent.id),
        )
        await db.commit()
    except AvatarValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except MinioObjectError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        if uploaded_object_name:
            try:
                AgentIconService(db).storage.delete(uploaded_object_name)
            except MinioObjectError:
                pass
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Иконка загружена в MinIO, но не сохранилась в базе данных.",
        ) from exc

    return await _agent_read(db, updated)
