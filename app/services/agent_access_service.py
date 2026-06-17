from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.user import DepartmentAgent, User, UserAgent
from app.schemas.agent import AgentAccessUpdate


class AgentAccessServiceError(ValueError):
    pass


class AgentAccessService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_access(
        self,
        agent_id: uuid.UUID,
    ) -> tuple[list[DepartmentAgent], list[UserAgent]]:
        agent = await self.db.get(Agent, agent_id)
        if agent is None:
            raise AgentAccessServiceError("Агент не найден")

        department_grants = await self.db.execute(
            select(DepartmentAgent).where(DepartmentAgent.agent_id == agent_id)
        )
        user_grants = await self.db.execute(
            select(UserAgent).where(UserAgent.agent_id == agent_id)
        )
        return list(department_grants.scalars().all()), list(user_grants.scalars().all())

    async def replace_access(
        self,
        agent_id: uuid.UUID,
        payload: AgentAccessUpdate,
        *,
        current_user: User,
    ) -> tuple[list[DepartmentAgent], list[UserAgent]]:
        if not current_user.is_superuser:
            raise AgentAccessServiceError("Изменять доступ к агенту может только администратор")

        agent = await self.db.get(Agent, agent_id)
        if agent is None:
            raise AgentAccessServiceError("Агент не найден")

        existing_departments = await self.db.execute(
            select(DepartmentAgent).where(DepartmentAgent.agent_id == agent_id)
        )
        for item in existing_departments.scalars().all():
            await self.db.delete(item)

        existing_users = await self.db.execute(
            select(UserAgent).where(UserAgent.agent_id == agent_id)
        )
        for item in existing_users.scalars().all():
            await self.db.delete(item)

        await self.db.flush()

        department_grants = [
            DepartmentAgent(
                agent_id=agent_id,
                department_id=item.department_id,
                access_level=item.access_level,
                can_run=item.can_run,
                can_view_results=item.can_view_results,
                can_approve=item.can_approve,
                can_configure=item.can_configure,
            )
            for item in payload.department_grants
        ]
        user_grants = [
            UserAgent(
                agent_id=agent_id,
                user_id=item.user_id,
                access_level=item.access_level,
                can_run=item.can_run,
                can_view_results=item.can_view_results,
                can_approve=item.can_approve,
                can_configure=item.can_configure,
                expires_at=item.expires_at,
                granted_by=current_user.id,
            )
            for item in payload.user_grants
        ]
        self.db.add_all([*department_grants, *user_grants])
        await self.db.flush()
        return department_grants, user_grants
