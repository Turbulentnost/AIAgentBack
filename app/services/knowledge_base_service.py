from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.integrations.qdrant import qdrant_client
from app.models.audit import AuditLog
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.enums import (
    KnowledgeBaseAccessType,
    KnowledgeBaseGrantType,
    KnowledgeBaseIndexJobStatus,
    KnowledgeBaseSourcePrecheckStatus,
    KnowledgeBaseSourceStatus,
    KnowledgeBaseStatus,
)
from app.models.knowledge_base import (
    KnowledgeBase,
    KnowledgeBaseAccessException,
    KnowledgeBaseAccessGrant,
    KnowledgeBaseAgentBinding,
    KnowledgeBaseChunk,
    KnowledgeBaseIndexingError,
    KnowledgeBaseIndexingJob,
    KnowledgeBaseRule,
    KnowledgeBaseSource,
)
from app.models.user import User
from app.schemas.knowledge_base import (
    KnowledgeBaseAccessUpdate,
    KnowledgeBaseAgentBindingInput,
    KnowledgeBaseCreate,
    KnowledgeBaseRuleCreate,
    KnowledgeBaseSourceCreate,
    KnowledgeBaseStats,
    KnowledgeBaseUpdate,
)
from app.services.audit_service import AuditService
from app.services.knowledge_base_access_service import KnowledgeBaseAccessService


class KnowledgeBaseServiceError(ValueError):
    pass


class KnowledgeBaseService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    async def list_knowledge_bases(
        self,
        *,
        status: KnowledgeBaseStatus | None = None,
        department_id: uuid.UUID | None = None,
        responsible_user_id: uuid.UUID | None = None,
        query: str | None = None,
    ) -> list[KnowledgeBase]:
        stmt = select(KnowledgeBase).order_by(KnowledgeBase.updated_at.desc())
        stmt = stmt.where(KnowledgeBase.deleted_at.is_(None))
        if status is not None:
            stmt = stmt.where(KnowledgeBase.status == status)
        if department_id is not None:
            stmt = stmt.where(KnowledgeBase.department_id == department_id)
        if responsible_user_id is not None:
            stmt = stmt.where(KnowledgeBase.responsible_user_id == responsible_user_id)
        if query:
            stmt = stmt.where(KnowledgeBase.name.ilike(f"%{query}%"))
        result = await self.db.execute(stmt)
        return list(result.scalars().unique().all())

    async def get(self, knowledge_base_id: uuid.UUID) -> KnowledgeBase | None:
        result = await self.db.execute(
            select(KnowledgeBase)
            .where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.deleted_at.is_(None),
            )
            .options(
                selectinload(KnowledgeBase.sources),
                selectinload(KnowledgeBase.rules),
                selectinload(KnowledgeBase.access_grants),
                selectinload(KnowledgeBase.access_exceptions),
                selectinload(KnowledgeBase.agent_bindings),
            )
        )
        return result.scalar_one_or_none()

    async def create(self, payload: KnowledgeBaseCreate, *, current_user: User) -> KnowledgeBase:
        if not payload.access_grants:
            raise KnowledgeBaseServiceError("Для базы знаний нужно явно указать права доступа")

        kb_id = uuid.uuid4()
        kb = KnowledgeBase(
            id=kb_id,
            name=payload.name,
            description=payload.description,
            department_id=payload.department_id or current_user.department_id,
            owner_user_id=current_user.id,
            responsible_user_id=payload.responsible_user_id or current_user.id,
            topic=payload.topic,
            process_slug=payload.process_slug,
            status=KnowledgeBaseStatus.DRAFT,
            embedding_model=payload.embedding_model or settings.EMBEDDINGS_MODEL,
            vector_store="qdrant",
            qdrant_collection=f"kb_{kb_id.hex}",
            is_public=False,
            metadata_=payload.metadata,
        )
        self.db.add(kb)
        await self.db.flush()

        for grant_payload in payload.access_grants:
            self.db.add(
                KnowledgeBaseAccessGrant(
                    knowledge_base_id=kb.id,
                    grantee_type=grant_payload.grantee_type,
                    grantee_id=grant_payload.grantee_id,
                    access_type=grant_payload.access_type,
                    include_child_departments=grant_payload.include_child_departments,
                    expires_at=grant_payload.expires_at,
                    reason=grant_payload.reason,
                    comment=grant_payload.comment,
                    responsible_user_id=grant_payload.responsible_user_id,
                    granted_by_user_id=current_user.id,
                )
            )

        await self._ensure_mandatory_user_grants(kb.id, payload, current_user)

        for document_id in payload.source_document_ids:
            await self.add_source(kb.id, KnowledgeBaseSourceCreate(document_id=document_id), current_user=current_user)

        await qdrant_client.ensure_collection(collection=kb.qdrant_collection, vector_size=settings.EMBEDDINGS_VECTOR_SIZE)
        await self.audit.log(
            action="kb.created",
            actor_id=current_user.id,
            resource_type="knowledge_base",
            resource_id=str(kb.id),
            payload={"name": kb.name, "qdrant_collection": kb.qdrant_collection},
        )
        await self.db.flush()
        return kb

    async def _ensure_mandatory_user_grants(
        self,
        knowledge_base_id: uuid.UUID,
        payload: KnowledgeBaseCreate,
        current_user: User,
    ) -> None:
        existing_user_ids = {
            grant.grantee_id
            for grant in payload.access_grants
            if grant.grantee_type == KnowledgeBaseGrantType.USER and grant.grantee_id is not None
        }
        mandatory_user_ids: set[uuid.UUID] = {current_user.id}
        if payload.responsible_user_id:
            mandatory_user_ids.add(payload.responsible_user_id)

        if payload.source_document_ids:
            result = await self.db.execute(
                select(Document.uploaded_by_user_id).where(
                    Document.id.in_(payload.source_document_ids),
                    Document.uploaded_by_user_id.is_not(None),
                )
            )
            mandatory_user_ids.update(user_id for (user_id,) in result.all() if user_id)

        for user_id in mandatory_user_ids:
            if user_id in existing_user_ids:
                continue
            self.db.add(
                KnowledgeBaseAccessGrant(
                    knowledge_base_id=knowledge_base_id,
                    grantee_type=KnowledgeBaseGrantType.USER,
                    grantee_id=user_id,
                    access_type=KnowledgeBaseAccessType.ADMIN,
                    reason="Автоматический доступ владельца или загрузившего источники",
                    granted_by_user_id=current_user.id,
                )
            )

    async def update(
        self,
        knowledge_base_id: uuid.UUID,
        payload: KnowledgeBaseUpdate,
        *,
        current_user: User,
    ) -> KnowledgeBase:
        kb = await self.get_or_raise(knowledge_base_id)
        update_data = payload.model_dump(exclude_unset=True)
        metadata = update_data.pop("metadata", None)
        for key, value in update_data.items():
            setattr(kb, key, value)
        if metadata is not None:
            kb.metadata_ = metadata
        await self.audit.log(
            action="kb.settings_updated",
            actor_id=current_user.id,
            resource_type="knowledge_base",
            resource_id=str(kb.id),
            payload=update_data,
        )
        await self.db.flush()
        return kb

    async def archive(self, knowledge_base_id: uuid.UUID, *, current_user: User) -> KnowledgeBase:
        """Soft-delete: mark KB as deleted in PostgreSQL only.

        Qdrant vectors, indexing jobs, sources and chunks are intentionally preserved.
        """
        result = await self.db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
        )
        kb = result.scalar_one_or_none()
        if kb is None:
            raise KnowledgeBaseServiceError("База знаний не найдена")
        if kb.deleted_at is not None:
            raise KnowledgeBaseServiceError("База знаний уже удалена")
        if not current_user.is_superuser and kb.owner_user_id != current_user.id:
            raise KnowledgeBaseServiceError("Удалить базу знаний может только её создатель")

        now = datetime.now(timezone.utc)
        kb.status = KnowledgeBaseStatus.ARCHIVED
        kb.deleted_at = now
        kb.deleted_by_user_id = current_user.id
        await self.audit.log(
            action="kb.archived",
            actor_id=current_user.id,
            resource_type="knowledge_base",
            resource_id=str(kb.id),
            payload={"soft_delete": True, "qdrant_collection": kb.qdrant_collection},
        )
        await self.db.flush()
        return kb

    async def confirm_review(self, knowledge_base_id: uuid.UUID, *, current_user: User) -> KnowledgeBase:
        kb = await self.get_or_raise(knowledge_base_id)
        if kb.deleted_at is not None:
            raise KnowledgeBaseServiceError("База знаний удалена")
        if kb.status == KnowledgeBaseStatus.READY:
            return kb
        if kb.status != KnowledgeBaseStatus.NEEDS_REVIEW:
            raise KnowledgeBaseServiceError("Подтверждение доступно только для баз со статусом «Требует проверки»")

        active_ids = await self.active_indexing_knowledge_base_ids([kb.id])
        if kb.id in active_ids:
            raise KnowledgeBaseServiceError("Нельзя подтвердить базу знаний во время индексации")

        access_service = KnowledgeBaseAccessService(self.db)
        kb_loaded = await access_service.load_for_access_check(kb.id) or kb
        admin_access = await access_service.can_access_knowledge_base(
            user=current_user,
            knowledge_base=kb_loaded,
            required_access=KnowledgeBaseAccessType.ADMIN,
        )
        can_manage = (
            current_user.is_superuser
            or kb.owner_user_id == current_user.id
            or kb.responsible_user_id == current_user.id
            or admin_access.allowed
        )
        if not can_manage:
            raise KnowledgeBaseServiceError("Недостаточно прав для подтверждения базы знаний")

        kb.status = KnowledgeBaseStatus.READY
        metadata = dict(kb.metadata_ or {})
        qc_warnings = metadata.get("qc_warnings")
        if isinstance(qc_warnings, dict):
            metadata["qc_warnings"] = {}
        kb.metadata_ = metadata

        await self.audit.log(
            action="kb.review_confirmed",
            actor_id=current_user.id,
            resource_type="knowledge_base",
            resource_id=str(kb.id),
            payload={"previous_status": KnowledgeBaseStatus.NEEDS_REVIEW.value},
        )
        await self.db.flush()
        return kb

    async def add_source(
        self,
        knowledge_base_id: uuid.UUID,
        payload: KnowledgeBaseSourceCreate,
        *,
        current_user: User,
    ) -> KnowledgeBaseSource:
        kb = await self.get_or_raise(knowledge_base_id)
        document = await self.db.get(Document, payload.document_id)
        if document is None:
            raise KnowledgeBaseServiceError("Документ-источник не найден")

        version = await self._resolve_document_version(document.id, payload.document_version_id)
        existing = await self.db.scalar(
            select(KnowledgeBaseSource).where(
                KnowledgeBaseSource.knowledge_base_id == kb.id,
                KnowledgeBaseSource.document_version_id == version.id,
            )
        )
        if existing is not None:
            return existing

        source = KnowledgeBaseSource(
            knowledge_base_id=kb.id,
            document_id=document.id,
            document_version_id=version.id,
            added_by_user_id=current_user.id,
            processing_status=KnowledgeBaseSourceStatus.DRAFT,
            file_size=version.file_size or document.file_size,
            checksum=document.checksum or version.checksum,
            access_snapshot=self._document_access_snapshot(document),
        )
        self.db.add(source)
        kb.sources_count += 1
        kb.storage_bytes += int(source.file_size or 0)
        await self.db.flush()

        from app.services.knowledge_base_precheck_service import KnowledgeBasePrecheckService

        precheck = await KnowledgeBasePrecheckService(self.db).precheck_source(source, user=current_user)
        if not precheck.passed:
            source.precheck_status = KnowledgeBaseSourcePrecheckStatus.FAILED
            source.precheck_notes = precheck.user_message
            source.processing_status = KnowledgeBaseSourceStatus.ERROR
        elif precheck.needs_ocr:
            source.processing_status = KnowledgeBaseSourceStatus.NEEDS_OCR
        else:
            source.processing_status = KnowledgeBaseSourceStatus.READY_TO_INDEX
        await self.db.flush()
        await self.audit.log(
            action="kb.source_added",
            actor_id=current_user.id,
            resource_type="knowledge_base",
            resource_id=str(kb.id),
            payload={"source_id": str(source.id), "document_id": str(document.id), "document_version_id": str(version.id)},
        )
        return source

    async def exclude_source(self, knowledge_base_id: uuid.UUID, source_id: uuid.UUID, *, current_user: User) -> KnowledgeBaseSource:
        source = await self.db.get(KnowledgeBaseSource, source_id)
        if source is None or source.knowledge_base_id != knowledge_base_id:
            raise KnowledgeBaseServiceError("Источник базы знаний не найден")
        source.processing_status = KnowledgeBaseSourceStatus.EXCLUDED
        await self.audit.log(
            action="kb.source_excluded",
            actor_id=current_user.id,
            resource_type="knowledge_base",
            resource_id=str(knowledge_base_id),
            payload={"source_id": str(source_id)},
        )
        await self.db.flush()
        return source

    async def remove_source(self, knowledge_base_id: uuid.UUID, source_id: uuid.UUID, *, current_user: User) -> None:
        source = await self.db.get(KnowledgeBaseSource, source_id)
        if source is None or source.knowledge_base_id != knowledge_base_id:
            raise KnowledgeBaseServiceError("Источник базы знаний не найден")
        await self.db.delete(source)
        await self.audit.log(
            action="kb.source_removed",
            actor_id=current_user.id,
            resource_type="knowledge_base",
            resource_id=str(knowledge_base_id),
            payload={"source_id": str(source_id)},
        )
        await self.db.flush()

    async def list_sources(self, knowledge_base_id: uuid.UUID) -> list[KnowledgeBaseSource]:
        await self.get_or_raise(knowledge_base_id)
        result = await self.db.execute(
            select(KnowledgeBaseSource)
            .where(KnowledgeBaseSource.knowledge_base_id == knowledge_base_id)
            .order_by(KnowledgeBaseSource.added_at.desc())
        )
        return list(result.scalars().all())

    async def list_chunks(self, knowledge_base_id: uuid.UUID) -> list[tuple[KnowledgeBaseChunk, DocumentChunk, Document | None]]:
        await self.get_or_raise(knowledge_base_id)
        result = await self.db.execute(
            select(KnowledgeBaseChunk, DocumentChunk, Document)
            .join(DocumentChunk, DocumentChunk.id == KnowledgeBaseChunk.document_chunk_id)
            .outerjoin(Document, Document.id == DocumentChunk.document_id)
            .where(KnowledgeBaseChunk.knowledge_base_id == knowledge_base_id)
            .order_by(KnowledgeBaseChunk.created_at.desc())
        )
        return list(result.all())

    async def exclude_chunk(
        self,
        knowledge_base_id: uuid.UUID,
        kb_chunk_id: uuid.UUID,
        *,
        is_excluded: bool,
        reason: str | None,
        current_user: User,
    ) -> KnowledgeBaseChunk:
        kb_chunk = await self.db.get(KnowledgeBaseChunk, kb_chunk_id)
        if kb_chunk is None or kb_chunk.knowledge_base_id != knowledge_base_id:
            raise KnowledgeBaseServiceError("Фрагмент базы знаний не найден")
        kb_chunk.is_excluded_from_search = is_excluded
        kb_chunk.exclusion_reason = reason
        kb_chunk.excluded_by_user_id = current_user.id if is_excluded else None
        await self.audit.log(
            action="kb.chunk_excluded" if is_excluded else "kb.chunk_restored",
            actor_id=current_user.id,
            resource_type="knowledge_base",
            resource_id=str(knowledge_base_id),
            payload={"knowledge_base_chunk_id": str(kb_chunk.id), "reason": reason},
        )
        await self.db.flush()
        return kb_chunk

    async def create_rule(
        self,
        knowledge_base_id: uuid.UUID,
        payload: KnowledgeBaseRuleCreate,
        *,
        current_user: User,
    ) -> KnowledgeBaseRule:
        await self.get_or_raise(knowledge_base_id)
        rule = KnowledgeBaseRule(knowledge_base_id=knowledge_base_id, **payload.model_dump())
        self.db.add(rule)
        await self.db.flush()
        await self.audit.log(
            action="kb.rule_created",
            actor_id=current_user.id,
            resource_type="knowledge_base",
            resource_id=str(knowledge_base_id),
            payload={"rule_id": str(rule.id), "priority": rule.priority},
        )
        return rule

    async def list_rules(self, knowledge_base_id: uuid.UUID) -> list[KnowledgeBaseRule]:
        await self.get_or_raise(knowledge_base_id)
        result = await self.db.execute(
            select(KnowledgeBaseRule)
            .where(KnowledgeBaseRule.knowledge_base_id == knowledge_base_id)
            .order_by(KnowledgeBaseRule.priority.asc(), KnowledgeBaseRule.created_at.desc())
        )
        return list(result.scalars().all())

    async def replace_access(
        self,
        knowledge_base_id: uuid.UUID,
        payload: KnowledgeBaseAccessUpdate,
        *,
        current_user: User,
    ) -> tuple[list[KnowledgeBaseAccessGrant], list[KnowledgeBaseAccessException]]:
        if not payload.grants:
            raise KnowledgeBaseServiceError("База знаний не может быть без явных прав доступа")
        kb = await self.get_or_raise(knowledge_base_id)
        for item in list(kb.access_grants):
            await self.db.delete(item)
        for item in list(kb.access_exceptions):
            await self.db.delete(item)
        await self.db.flush()

        grants = [
            KnowledgeBaseAccessGrant(
                knowledge_base_id=kb.id,
                granted_by_user_id=current_user.id,
                **item.model_dump(),
            )
            for item in payload.grants
        ]
        exceptions = [
            KnowledgeBaseAccessException(
                knowledge_base_id=kb.id,
                granted_by_user_id=current_user.id,
                **item.model_dump(),
            )
            for item in payload.exceptions
        ]
        self.db.add_all([*grants, *exceptions])
        await self.audit.log(
            action="kb.access_replaced",
            actor_id=current_user.id,
            resource_type="knowledge_base",
            resource_id=str(kb.id),
            payload={"grants": len(grants), "exceptions": len(exceptions)},
        )
        await self.db.flush()
        return grants, exceptions

    async def list_access(
        self,
        knowledge_base_id: uuid.UUID,
    ) -> tuple[list[KnowledgeBaseAccessGrant], list[KnowledgeBaseAccessException]]:
        await self.get_or_raise(knowledge_base_id)
        grants = await self.db.execute(
            select(KnowledgeBaseAccessGrant).where(KnowledgeBaseAccessGrant.knowledge_base_id == knowledge_base_id)
        )
        exceptions = await self.db.execute(
            select(KnowledgeBaseAccessException).where(
                KnowledgeBaseAccessException.knowledge_base_id == knowledge_base_id
            )
        )
        return list(grants.scalars().all()), list(exceptions.scalars().all())

    async def replace_agents(
        self,
        knowledge_base_id: uuid.UUID,
        payload: list[KnowledgeBaseAgentBindingInput],
        *,
        current_user: User,
    ) -> list[KnowledgeBaseAgentBinding]:
        kb = await self.get_or_raise(knowledge_base_id)
        for item in list(kb.agent_bindings):
            await self.db.delete(item)
        await self.db.flush()
        bindings = [
            KnowledgeBaseAgentBinding(knowledge_base_id=kb.id, **item.model_dump())
            for item in payload
        ]
        self.db.add_all(bindings)
        await self.audit.log(
            action="kb.agents_replaced",
            actor_id=current_user.id,
            resource_type="knowledge_base",
            resource_id=str(kb.id),
            payload={"agents": len(bindings)},
        )
        await self.db.flush()
        return bindings

    async def list_agents(self, knowledge_base_id: uuid.UUID) -> list[KnowledgeBaseAgentBinding]:
        await self.get_or_raise(knowledge_base_id)
        result = await self.db.execute(
            select(KnowledgeBaseAgentBinding).where(KnowledgeBaseAgentBinding.knowledge_base_id == knowledge_base_id)
        )
        return list(result.scalars().all())

    async def list_jobs(self, knowledge_base_id: uuid.UUID) -> list[KnowledgeBaseIndexingJob]:
        await self.get_or_raise(knowledge_base_id)
        result = await self.db.execute(
            select(KnowledgeBaseIndexingJob)
            .where(KnowledgeBaseIndexingJob.knowledge_base_id == knowledge_base_id)
            .order_by(KnowledgeBaseIndexingJob.created_at.desc())
        )
        return list(result.scalars().all())

    async def active_indexing_knowledge_base_ids(self, knowledge_base_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        if not knowledge_base_ids:
            return set()
        result = await self.db.execute(
            select(KnowledgeBaseIndexingJob.knowledge_base_id)
            .where(
                KnowledgeBaseIndexingJob.knowledge_base_id.in_(knowledge_base_ids),
                KnowledgeBaseIndexingJob.status.in_(
                    [KnowledgeBaseIndexJobStatus.QUEUED, KnowledgeBaseIndexJobStatus.RUNNING]
                ),
            )
            .distinct()
        )
        return {row[0] for row in result.all()}

    async def list_job_errors(self, job_id: uuid.UUID) -> list[KnowledgeBaseIndexingError]:
        result = await self.db.execute(
            select(KnowledgeBaseIndexingError)
            .where(KnowledgeBaseIndexingError.job_id == job_id)
            .order_by(KnowledgeBaseIndexingError.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_audit(self, knowledge_base_id: uuid.UUID) -> list[AuditLog]:
        await self.get_or_raise(knowledge_base_id)
        result = await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "knowledge_base",
                AuditLog.resource_id == str(knowledge_base_id),
            )
            .order_by(AuditLog.created_at.desc())
        )
        return list(result.scalars().all())

    async def stats(self, *, user: User) -> KnowledgeBaseStats:
        access_service = KnowledgeBaseAccessService(self.db)
        accessible_ids: list[uuid.UUID] = []
        successfully_indexed = 0

        for kb in await self.list_knowledge_bases():
            kb_loaded = await access_service.load_for_access_check(kb.id) or kb
            read_access = await access_service.can_access_knowledge_base(
                user=user,
                knowledge_base=kb_loaded,
                required_access=KnowledgeBaseAccessType.READ,
            )
            search_access = await access_service.can_access_knowledge_base(
                user=user,
                knowledge_base=kb_loaded,
                required_access=KnowledgeBaseAccessType.SEARCH,
                allow_non_ready_for_admin=False,
            )
            if not read_access.allowed and not search_access.allowed:
                continue
            accessible_ids.append(kb.id)
            if kb.status == KnowledgeBaseStatus.READY:
                successfully_indexed += 1

        if not accessible_ids:
            return KnowledgeBaseStats(
                total_bases=0,
                indexing_errors_count=0,
                storage_bytes=0,
                successfully_indexed_bases=0,
            )

        storage = await self.db.scalar(
            select(func.coalesce(func.sum(KnowledgeBase.storage_bytes), 0)).where(
                KnowledgeBase.id.in_(accessible_ids)
            )
        )
        errors = await self.db.scalar(
            select(func.count(KnowledgeBaseIndexingError.id)).where(
                KnowledgeBaseIndexingError.is_resolved.is_(False),
                KnowledgeBaseIndexingError.knowledge_base_id.in_(accessible_ids),
            )
        )
        return KnowledgeBaseStats(
            total_bases=len(accessible_ids),
            indexing_errors_count=int(errors or 0),
            storage_bytes=int(storage or 0),
            successfully_indexed_bases=successfully_indexed,
        )

    async def get_or_raise(self, knowledge_base_id: uuid.UUID) -> KnowledgeBase:
        kb = await self.get(knowledge_base_id)
        if kb is None:
            raise KnowledgeBaseServiceError("База знаний не найдена")
        return kb

    async def _resolve_document_version(
        self,
        document_id: uuid.UUID,
        document_version_id: uuid.UUID | None,
    ) -> DocumentVersion:
        if document_version_id is not None:
            version = await self.db.get(DocumentVersion, document_version_id)
            if version is None or version.document_id != document_id:
                raise KnowledgeBaseServiceError("Версия документа не найдена")
            return version
        result = await self.db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.is_current.desc(), DocumentVersion.version_number.desc())
            .limit(1)
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise KnowledgeBaseServiceError("У документа нет версий")
        return version

    def _document_access_snapshot(self, document: Document) -> dict:
        return {
            "document_id": str(document.id),
            "department_id": str(document.department_id) if document.department_id else None,
            "access_scope": (document.metadata_ or {}).get("access_scope", "department"),
        }


def file_extension(filename: str | None) -> str | None:
    if not filename:
        return None
    suffix = Path(filename).suffix.lower()
    return suffix or None
