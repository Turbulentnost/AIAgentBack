from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
from app.models.document import Document
from app.models.enums import (
    AgentStatus,
    KnowledgeBaseAccessType,
    KnowledgeBaseAgentAccessMode,
    KnowledgeBaseGrantType,
    KnowledgeBaseStatus,
)
from app.models.knowledge_base import (
    KnowledgeBase,
    KnowledgeBaseAccessException,
    KnowledgeBaseAccessGrant,
    KnowledgeBaseAgentBinding,
    KnowledgeBaseChunk,
)
from app.models.user import Department, User
from app.services.permission_service import PermissionService


ACCESS_RANK: dict[KnowledgeBaseAccessType, int] = {
    KnowledgeBaseAccessType.READ: 10,
    KnowledgeBaseAccessType.SEARCH: 20,
    KnowledgeBaseAccessType.USE_VIA_AGENT: 30,
    KnowledgeBaseAccessType.MANAGE_SOURCES: 40,
    KnowledgeBaseAccessType.REINDEX: 50,
    KnowledgeBaseAccessType.MANAGE_ACCESS: 60,
    KnowledgeBaseAccessType.ADMIN: 70,
}

AGENT_MODE_RANK: dict[KnowledgeBaseAgentAccessMode, int] = {
    KnowledgeBaseAgentAccessMode.SEARCH_ONLY: 10,
    KnowledgeBaseAgentAccessMode.SEARCH_AND_CITE: 20,
    KnowledgeBaseAgentAccessMode.DECISION: 30,
    KnowledgeBaseAgentAccessMode.AUTO_ACTION: 40,
}


@dataclass(frozen=True)
class EffectiveKnowledgeBaseAccess:
    allowed: bool
    reason: str
    access_type: KnowledgeBaseAccessType | None = None
    agent_mode: KnowledgeBaseAgentAccessMode | None = None


class KnowledgeBaseAccessService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.permission_service = PermissionService(db)

    async def can_access_knowledge_base(
        self,
        *,
        user: User,
        knowledge_base: KnowledgeBase,
        required_access: KnowledgeBaseAccessType = KnowledgeBaseAccessType.READ,
        agent_id: uuid.UUID | None = None,
        required_agent_mode: KnowledgeBaseAgentAccessMode = KnowledgeBaseAgentAccessMode.SEARCH_ONLY,
        allow_non_ready_for_admin: bool = True,
    ) -> EffectiveKnowledgeBaseAccess:
        if user.is_superuser:
            return EffectiveKnowledgeBaseAccess(True, "superuser", KnowledgeBaseAccessType.ADMIN)

        if knowledge_base.status == KnowledgeBaseStatus.ARCHIVED:
            return EffectiveKnowledgeBaseAccess(False, "knowledge_base_archived")

        if knowledge_base.status != KnowledgeBaseStatus.READY and not allow_non_ready_for_admin:
            return EffectiveKnowledgeBaseAccess(False, "knowledge_base_not_ready")

        access_type = await self._best_user_access(user, knowledge_base.id)
        if access_type is None:
            return EffectiveKnowledgeBaseAccess(False, "no_knowledge_base_grant")

        if ACCESS_RANK[access_type] < ACCESS_RANK[required_access]:
            return EffectiveKnowledgeBaseAccess(False, "insufficient_knowledge_base_grant", access_type)

        if agent_id is not None:
            agent_access = await self.can_agent_use_knowledge_base(
                knowledge_base=knowledge_base,
                agent_id=agent_id,
                required_mode=required_agent_mode,
            )
            if not agent_access.allowed:
                return agent_access
            return EffectiveKnowledgeBaseAccess(True, "allowed", access_type, agent_access.agent_mode)

        return EffectiveKnowledgeBaseAccess(True, "allowed", access_type)

    async def can_use_chunk(
        self,
        *,
        user: User,
        knowledge_base: KnowledgeBase,
        kb_chunk: KnowledgeBaseChunk,
        document: Document | None,
        agent_id: uuid.UUID | None = None,
        required_access: KnowledgeBaseAccessType = KnowledgeBaseAccessType.SEARCH,
        required_agent_mode: KnowledgeBaseAgentAccessMode = KnowledgeBaseAgentAccessMode.SEARCH_ONLY,
    ) -> EffectiveKnowledgeBaseAccess:
        kb_access = await self.can_access_knowledge_base(
            user=user,
            knowledge_base=knowledge_base,
            required_access=required_access,
            agent_id=agent_id,
            required_agent_mode=required_agent_mode,
            allow_non_ready_for_admin=False,
        )
        if not kb_access.allowed:
            return kb_access

        if kb_chunk.is_excluded_from_search:
            return EffectiveKnowledgeBaseAccess(False, "chunk_excluded", kb_access.access_type, kb_access.agent_mode)

        if document is None:
            return EffectiveKnowledgeBaseAccess(False, "source_document_missing", kb_access.access_type, kb_access.agent_mode)

        if getattr(document.processing_status, "value", document.processing_status) == "archived":
            return EffectiveKnowledgeBaseAccess(False, "source_document_archived", kb_access.access_type, kb_access.agent_mode)

        if not await self.permission_service.can_access_document(user, document.id):
            return EffectiveKnowledgeBaseAccess(False, "source_document_denied", kb_access.access_type, kb_access.agent_mode)

        return EffectiveKnowledgeBaseAccess(True, "allowed", kb_access.access_type, kb_access.agent_mode)

    async def can_agent_use_knowledge_base(
        self,
        *,
        knowledge_base: KnowledgeBase,
        agent_id: uuid.UUID,
        required_mode: KnowledgeBaseAgentAccessMode = KnowledgeBaseAgentAccessMode.SEARCH_ONLY,
    ) -> EffectiveKnowledgeBaseAccess:
        now = _now()
        binding = await self.db.scalar(
            select(KnowledgeBaseAgentBinding).where(
                KnowledgeBaseAgentBinding.knowledge_base_id == knowledge_base.id,
                KnowledgeBaseAgentBinding.agent_id == agent_id,
                KnowledgeBaseAgentBinding.is_enabled.is_(True),
            )
        )
        if binding is None:
            return EffectiveKnowledgeBaseAccess(False, "agent_not_bound")
        if binding.expires_at is not None and _as_aware(binding.expires_at) <= now:
            return EffectiveKnowledgeBaseAccess(False, "agent_binding_expired")
        if AGENT_MODE_RANK[binding.access_mode] < AGENT_MODE_RANK[required_mode]:
            return EffectiveKnowledgeBaseAccess(False, "insufficient_agent_mode", agent_mode=binding.access_mode)

        agent = await self.db.get(Agent, agent_id)
        if agent is None or agent.status not in {AgentStatus.ACTIVE, AgentStatus.OPE, AgentStatus.TESTING}:
            return EffectiveKnowledgeBaseAccess(False, "agent_not_active", agent_mode=binding.access_mode)
        return EffectiveKnowledgeBaseAccess(True, "allowed", agent_mode=binding.access_mode)

    async def load_for_access_check(self, knowledge_base_id: uuid.UUID) -> KnowledgeBase | None:
        result = await self.db.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.id == knowledge_base_id)
            .options(
                selectinload(KnowledgeBase.access_grants),
                selectinload(KnowledgeBase.access_exceptions),
                selectinload(KnowledgeBase.agent_bindings),
            )
        )
        return result.scalar_one_or_none()

    async def _best_user_access(
        self,
        user: User,
        knowledge_base_id: uuid.UUID,
    ) -> KnowledgeBaseAccessType | None:
        grants = await self._active_grants(knowledge_base_id)
        exceptions = await self._active_exceptions(knowledge_base_id)

        denied = await self._matching_access_types(
            user=user,
            items=exceptions,
            deny_only=True,
        )
        allowed = await self._matching_access_types(
            user=user,
            items=grants,
            deny_only=False,
        )

        effective = [access for access in allowed if access not in denied]
        if not effective:
            return None
        return max(effective, key=lambda item: ACCESS_RANK[item])

    async def _active_grants(self, knowledge_base_id: uuid.UUID) -> list[KnowledgeBaseAccessGrant]:
        now = _now()
        result = await self.db.execute(
            select(KnowledgeBaseAccessGrant).where(
                KnowledgeBaseAccessGrant.knowledge_base_id == knowledge_base_id,
            )
        )
        return [
            grant
            for grant in result.scalars().all()
            if grant.expires_at is None or _as_aware(grant.expires_at) > now
        ]

    async def _active_exceptions(self, knowledge_base_id: uuid.UUID) -> list[KnowledgeBaseAccessException]:
        now = _now()
        result = await self.db.execute(
            select(KnowledgeBaseAccessException).where(
                KnowledgeBaseAccessException.knowledge_base_id == knowledge_base_id,
            )
        )
        return [
            exception
            for exception in result.scalars().all()
            if exception.expires_at is None or _as_aware(exception.expires_at) > now
        ]

    async def _matching_access_types(
        self,
        *,
        user: User,
        items: list[KnowledgeBaseAccessGrant] | list[KnowledgeBaseAccessException],
        deny_only: bool,
    ) -> list[KnowledgeBaseAccessType]:
        matched: list[KnowledgeBaseAccessType] = []
        department_ids = await self._department_scope(user.department_id)
        for item in items:
            if deny_only and not getattr(item, "is_deny", False):
                continue
            if item.grantee_type == KnowledgeBaseGrantType.USER and item.grantee_id == user.id:
                matched.append(item.access_type)
            elif item.grantee_type == KnowledgeBaseGrantType.DEPARTMENT:
                include_children = bool(getattr(item, "include_child_departments", False))
                if item.grantee_id == user.department_id or (include_children and item.grantee_id in department_ids):
                    matched.append(item.access_type)
            elif item.grantee_type == KnowledgeBaseGrantType.ADMIN_ONLY:
                continue
        return matched

    async def _department_scope(self, department_id: uuid.UUID | None) -> set[uuid.UUID]:
        if department_id is None:
            return set()
        result = await self.db.execute(select(Department.id, Department.parent_id))
        parents = {row.id: row.parent_id for row in result.all()}
        scope = {department_id}
        current = department_id
        while parents.get(current) is not None:
            current = parents[current]
            scope.add(current)
        return scope


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)
