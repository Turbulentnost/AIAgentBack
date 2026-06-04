from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.document import Document
from app.models.task import Task
from app.models.user import DepartmentAgent, User, UserAgent


class PermissionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_available_agents(self, user: User) -> list[Agent]:
        if user.is_superuser:
            result = await self.db.execute(select(Agent).order_by(Agent.name))
            return list(result.scalars().unique().all())

        now = datetime.now(timezone.utc)
        conditions = [
            UserAgent.user_id == user.id,
            or_(UserAgent.expires_at.is_(None), UserAgent.expires_at > now),
        ]
        agent_ids_query = select(UserAgent.agent_id).where(*conditions)

        if user.department_id is not None:
            department_agent_ids = select(DepartmentAgent.agent_id).where(
                DepartmentAgent.department_id == user.department_id
            )
            agent_ids_query = agent_ids_query.union(department_agent_ids)

        result = await self.db.execute(
            select(Agent).where(Agent.id.in_(agent_ids_query)).order_by(Agent.name)
        )
        return list(result.scalars().unique().all())

    async def can_access_agent(self, user: User, agent_id: uuid.UUID, action: str = "run") -> bool:
        if user.is_superuser:
            return True

        column = self._agent_action_column(action, UserAgent)
        now = datetime.now(timezone.utc)
        user_access = await self.db.scalar(
            select(UserAgent).where(
                UserAgent.user_id == user.id,
                UserAgent.agent_id == agent_id,
                column.is_(True),
                or_(UserAgent.expires_at.is_(None), UserAgent.expires_at > now),
            )
        )
        if user_access is not None:
            return True

        if user.department_id is None:
            return False

        dept_column = self._agent_action_column(action, DepartmentAgent)
        dept_access = await self.db.scalar(
            select(DepartmentAgent).where(
                DepartmentAgent.department_id == user.department_id,
                DepartmentAgent.agent_id == agent_id,
                dept_column.is_(True),
            )
        )
        return dept_access is not None

    async def can_access_task(self, user: User, task_id: uuid.UUID) -> bool:
        if user.is_superuser:
            return True
        task = await self.db.get(Task, task_id)
        return task is not None and task.created_by_id == user.id

    async def can_access_document(self, user: User, document_id: uuid.UUID) -> bool:
        if user.is_superuser:
            return True
        document = await self.db.get(Document, document_id)
        if document is None:
            return False
        return user.department_id is not None and document.department_id == user.department_id

    def _agent_action_column(self, action: str, model: type[UserAgent] | type[DepartmentAgent]):
        mapping = {
            "run": model.can_run,
            "view_results": model.can_view_results,
            "approve": model.can_approve,
            "configure": model.can_configure,
        }
        return mapping.get(action, model.can_run)
