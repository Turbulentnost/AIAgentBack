from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import literal, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.document import Document
from app.models.task import Task
from app.models.user import DepartmentAgent, Permission, User, UserAgent, role_permissions, user_roles
from app.services.document_analysis_permission import (
    is_avion_only_platform_user,
    list_agents_for_avion_only_user,
)

PUBLIC_DOCUMENT_ACCESS_SCOPES = {"public", "global", "all", "company"}


class PermissionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_available_agents(self, user: User) -> list[Agent]:
        if is_avion_only_platform_user(user):
            return await list_agents_for_avion_only_user(self.db)

        if user.is_superuser:
            result = await self.db.execute(select(Agent).order_by(Agent.name))
            return list(result.scalars().unique().all())

        now = datetime.now(timezone.utc)
        conditions = [
            UserAgent.user_id == user.id,
            or_(UserAgent.expires_at.is_(None), UserAgent.expires_at > now),
        ]
        agent_id_queries = [select(UserAgent.agent_id).where(*conditions)]

        if user.department_id is not None:
            agent_id_queries.append(
                select(DepartmentAgent.agent_id).where(
                    DepartmentAgent.department_id == user.department_id
                )
            )

        role_agent_ids = self._role_agent_ids_query(user, "run")
        if role_agent_ids is not None:
            agent_id_queries.append(role_agent_ids)

        agent_ids_query = agent_id_queries[0] if len(agent_id_queries) == 1 else union(*agent_id_queries)

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

        if await self._has_role_agent_permission(user, agent_id, action):
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
        return self.can_access_document_record(user, document)

    def can_access_document_record(self, user: User, document: Document) -> bool:
        if user.is_superuser:
            return True
        if document.uploaded_by_user_id == user.id:
            return True
        if user.department_id is not None and document.department_id == user.department_id:
            return True
        metadata = document.metadata_ or {}
        access_scope = str(metadata.get("access_scope") or metadata.get("access") or "").lower()
        return access_scope in PUBLIC_DOCUMENT_ACCESS_SCOPES

    def _agent_action_column(self, action: str, model: type[UserAgent] | type[DepartmentAgent]):
        mapping = {
            "run": model.can_run,
            "view_results": model.can_view_results,
            "approve": model.can_approve,
            "configure": model.can_configure,
        }
        return mapping.get(action, model.can_run)

    def _role_agent_ids_query(self, user: User, action: str):
        role_ids_query = self._user_role_ids_query(user)
        if role_ids_query is None:
            return None
        return (
            select(Agent.id)
            .join(Permission, Permission.code == (literal("agents.") + Agent.slug + literal(f".{action}")))
            .join(role_permissions, role_permissions.c.permission_id == Permission.id)
            .where(role_permissions.c.role_id.in_(role_ids_query))
        )

    def _user_role_ids_query(self, user: User):
        role_queries = []
        if user.role_id is not None:
            role_queries.append(select(literal(user.role_id).label("role_id")))
        role_queries.append(select(user_roles.c.role_id).where(user_roles.c.user_id == user.id))
        if len(role_queries) == 1:
            return role_queries[0]
        return union(*role_queries)

    async def _has_role_agent_permission(self, user: User, agent_id: uuid.UUID, action: str) -> bool:
        role_ids_query = self._user_role_ids_query(user)
        if role_ids_query is None:
            return False
        result = await self.db.scalar(
            select(Permission.id)
            .select_from(Agent)
            .join(Permission, Permission.code == (literal("agents.") + Agent.slug + literal(f".{action}")))
            .join(role_permissions, role_permissions.c.permission_id == Permission.id)
            .where(
                Agent.id == agent_id,
                role_permissions.c.role_id.in_(role_ids_query),
            )
            .limit(1)
        )
        return result is not None
