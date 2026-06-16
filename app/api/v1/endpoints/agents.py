from __future__ import annotations
import uuid
from fastapi import APIRouter, HTTPException, status
from app.api.deps import CurrentUser, DbSession
from app.schemas.agent import AgentAccessRead, AgentCreate, AgentRead, AgentUpdate
from app.services.agent_service import AgentService
from app.services.permission_service import PermissionService
from app.services.nd_control_permission import append_nd_control_agent_for_quality_deputy
router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/available", response_model=list[AgentAccessRead])
async def list_available_agents(db: DbSession, current_user: CurrentUser):
    agents = await PermissionService(db).list_available_agents(current_user)
    agents = await append_nd_control_agent_for_quality_deputy(db, current_user, agents)
    return [
        AgentAccessRead.model_validate(
            {
                **AgentRead.model_validate(agent).model_dump(),
                "access_level": "full" if current_user.is_superuser else "granted",
                "can_run": True,
                "can_view_results": True,
                "can_approve": current_user.is_superuser,
                "can_configure": current_user.is_superuser,
            }
        )
        for agent in agents
    ]


@router.get("", response_model=list[AgentRead])
async def list_agents(db: DbSession, limit: int = 50, offset: int = 0):
    return await AgentService(db).list(limit, offset)
@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(db: DbSession, data: AgentCreate):
    return await AgentService(db).create(data)
@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(db: DbSession, agent_id: uuid.UUID):
    agent = await AgentService(db).get(agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Агент не найден")
    return agent
@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(db: DbSession, agent_id: uuid.UUID, data: AgentUpdate):
    service = AgentService(db)
    agent = await service.get(agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Агент не найден")
    return await service.update(agent, data)
