from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.enums import (
    KnowledgeBaseAccessType,
    NdChangeJournalEventType,
    NdChangeJournalSource,
    NdTemplateClassificationStatus,
    NdTemplateType,
)
from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseSource
from app.models.nd_control_templates import (
    NdControlTemplate,
    NdControlTemplateDocument,
    NdControlTemplateKnowledgeBase,
)
from app.models.user import User
from app.services.knowledge_base_access_service import KnowledgeBaseAccessService
from app.services.nd_change_journal_service import NdChangeJournalService
from app.services.nd_control_permission import can_manage_nd_control_templates, can_upload_template_documents
from app.utils.nd_template_classification import ND_TEMPLATE_TYPE_LABELS, get_template_type_label


class NdControlTemplateServiceError(Exception):
    pass


class NdControlTemplateService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.kb_access = KnowledgeBaseAccessService(db)

    async def list_templates(
        self,
        *,
        template_type: NdTemplateType | None = None,
        query: str | None = None,
        active_only: bool = True,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict], int]:
        kb_counts = (
            select(
                NdControlTemplateKnowledgeBase.template_id.label("template_id"),
                func.count(NdControlTemplateKnowledgeBase.id).label("knowledge_bases_count"),
            )
            .group_by(NdControlTemplateKnowledgeBase.template_id)
            .subquery()
        )
        doc_counts = (
            select(
                NdControlTemplateDocument.template_id.label("template_id"),
                func.count(NdControlTemplateDocument.id).label("documents_count"),
            )
            .group_by(NdControlTemplateDocument.template_id)
            .subquery()
        )
        stmt = (
            select(
                NdControlTemplate,
                func.coalesce(kb_counts.c.knowledge_bases_count, 0),
                func.coalesce(doc_counts.c.documents_count, 0),
            )
            .outerjoin(kb_counts, kb_counts.c.template_id == NdControlTemplate.id)
            .outerjoin(doc_counts, doc_counts.c.template_id == NdControlTemplate.id)
        )
        if active_only:
            stmt = stmt.where(NdControlTemplate.is_active.is_(True))
        if template_type is not None:
            stmt = stmt.where(NdControlTemplate.template_type == template_type)
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(
                    NdControlTemplate.name.ilike(pattern),
                    NdControlTemplate.description.ilike(pattern),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int(await self.db.scalar(count_stmt) or 0)
        offset = max(0, (page - 1) * size)
        result = await self.db.execute(
            stmt.order_by(NdControlTemplate.sort_order, NdControlTemplate.name)
            .offset(offset)
            .limit(size)
        )
        return [
            await self._template_row(template, int(kb_count or 0), int(doc_count or 0))
            for template, kb_count, doc_count in result.all()
        ], total

    async def list_sources(
        self,
        *,
        user: User,
        knowledge_base_id: uuid.UUID | None = None,
        query: str | None = None,
        include_registered: bool = False,
        limit: int = 200,
    ) -> list[dict]:
        registered_sources = (
            select(NdControlTemplateDocument.knowledge_base_source_id)
            .where(NdControlTemplateDocument.knowledge_base_source_id == KnowledgeBaseSource.id)
            .exists()
        )
        stmt = (
            select(KnowledgeBaseSource, KnowledgeBase, Document, registered_sources.label("already_registered"))
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeBaseSource.knowledge_base_id)
            .join(Document, Document.id == KnowledgeBaseSource.document_id)
            .where(KnowledgeBase.deleted_at.is_(None))
        )
        if knowledge_base_id is not None:
            stmt = stmt.where(KnowledgeBaseSource.knowledge_base_id == knowledge_base_id)
        if not include_registered:
            stmt = stmt.where(~registered_sources)
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(
                    KnowledgeBase.name.ilike(pattern),
                    Document.title.ilike(pattern),
                    Document.original_filename.ilike(pattern),
                )
            )
        result = await self.db.execute(stmt.order_by(KnowledgeBase.name, Document.title).limit(limit))

        items: list[dict] = []
        access_cache: dict[uuid.UUID, bool] = {}
        for source, kb, document, already_registered in result.all():
            allowed = access_cache.get(kb.id)
            if allowed is None:
                access = await self.kb_access.can_access_knowledge_base(
                    user=user,
                    knowledge_base=kb,
                    required_access=KnowledgeBaseAccessType.READ,
                )
                allowed = access.allowed
                access_cache[kb.id] = allowed
            if not allowed:
                continue
            items.append(
                {
                    "id": source.id,
                    "knowledge_base_id": source.knowledge_base_id,
                    "knowledge_base_name": kb.name,
                    "document_id": source.document_id,
                    "document_version_id": source.document_version_id,
                    "document_title": document.title,
                    "original_filename": document.original_filename,
                    "processing_status": getattr(source.processing_status, "value", source.processing_status),
                    "already_registered": bool(already_registered),
                }
            )
        return items

    async def create_template(
        self,
        *,
        template_type: NdTemplateType,
        current_user: User | None = None,
        name: str | None = None,
        description: str | None = None,
        sort_order: int | None = None,
    ) -> NdControlTemplate:
        if current_user is not None:
            await self._require_manage(current_user)
        existing = await self.db.scalar(
            select(NdControlTemplate).where(NdControlTemplate.template_type == template_type)
        )
        clean_name = (name or get_template_type_label(template_type) or template_type.value).strip()
        if existing is not None:
            existing.name = clean_name
            if description is not None:
                existing.description = description
            if sort_order is not None:
                existing.sort_order = sort_order
            existing.is_active = True
            await self.db.flush()
            return existing

        template = NdControlTemplate(
            template_type=template_type,
            name=clean_name,
            description=description,
            sort_order=sort_order if sort_order is not None else self._default_sort_order(template_type),
            created_by_user_id=current_user.id if current_user is not None else None,
        )
        self.db.add(template)
        await self.db.flush()
        return template

    async def update_template(
        self,
        template_id: uuid.UUID,
        *,
        current_user: User,
        values: dict,
    ) -> NdControlTemplate:
        await self._require_manage(current_user)
        template = await self.get_template_or_raise(template_id)
        for key, value in values.items():
            if hasattr(template, key):
                setattr(template, key, value)
        await self.db.flush()
        return template

    async def set_template_knowledge_bases(
        self,
        template_id: uuid.UUID,
        knowledge_base_ids: list[uuid.UUID],
        *,
        current_user: User,
    ) -> NdControlTemplate:
        await self._require_manage(current_user)
        template = await self.get_template_or_raise(template_id)
        await self._validate_kb_access(current_user, knowledge_base_ids)

        result = await self.db.execute(
            select(NdControlTemplateKnowledgeBase).where(
                NdControlTemplateKnowledgeBase.template_id == template.id
            )
        )
        existing_links = list(result.scalars().all())
        existing_ids = {link.knowledge_base_id for link in existing_links}
        new_ids = set(knowledge_base_ids)

        for link in existing_links:
            if link.knowledge_base_id not in new_ids:
                await self.db.delete(link)
        await self.db.flush()

        for kb_id in new_ids - existing_ids:
            self.db.add(
                NdControlTemplateKnowledgeBase(
                    template_id=template.id,
                    knowledge_base_id=kb_id,
                )
            )
        await self.db.flush()
        return template

    async def list_template_documents(
        self,
        template_id: uuid.UUID,
        *,
        classification_status: NdTemplateClassificationStatus | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict], int]:
        await self.get_template_or_raise(template_id)
        stmt = (
            select(NdControlTemplateDocument, KnowledgeBase, Document)
            .join(KnowledgeBase, KnowledgeBase.id == NdControlTemplateDocument.knowledge_base_id)
            .join(Document, Document.id == NdControlTemplateDocument.document_id)
            .where(NdControlTemplateDocument.template_id == template_id)
        )
        if classification_status is not None:
            stmt = stmt.where(NdControlTemplateDocument.classification_status == classification_status)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int(await self.db.scalar(count_stmt) or 0)
        offset = max(0, (page - 1) * size)
        result = await self.db.execute(
            stmt.order_by(NdControlTemplateDocument.created_at.desc()).offset(offset).limit(size)
        )
        return [
            self._document_row(link, kb, document)
            for link, kb, document in result.all()
        ], total

    async def get_template_document_detail_or_raise(
        self,
        template_id: uuid.UUID,
        document_link_id: uuid.UUID,
    ) -> dict:
        result = await self.db.execute(
            select(NdControlTemplateDocument, KnowledgeBase, Document)
            .join(KnowledgeBase, KnowledgeBase.id == NdControlTemplateDocument.knowledge_base_id)
            .join(Document, Document.id == NdControlTemplateDocument.document_id)
            .where(
                NdControlTemplateDocument.id == document_link_id,
                NdControlTemplateDocument.template_id == template_id,
            )
        )
        row = result.one_or_none()
        if row is None:
            raise NdControlTemplateServiceError("Документ шаблона не найден")
        link, kb, document = row
        return self._document_row(link, kb, document)

    async def confirm_template_document_type(
        self,
        template_id: uuid.UUID,
        document_link_id: uuid.UUID,
        *,
        current_user: User,
    ) -> dict:
        if not await can_upload_template_documents(self.db, current_user):
            raise NdControlTemplateServiceError("Недостаточно прав для подтверждения типа документа шаблона")
        template = await self.get_template_or_raise(template_id)
        link = await self.db.get(NdControlTemplateDocument, document_link_id)
        if link is None or link.template_id != template.id:
            raise NdControlTemplateServiceError("Документ шаблона не найден")
        metadata = link.metadata_ or {}
        metadata["manual_confirmation"] = {
            "confirmed_by_user_id": str(current_user.id),
            "previous_detected_template_type": getattr(link.detected_template_type, "value", link.detected_template_type),
            "previous_classification_status": getattr(link.classification_status, "value", link.classification_status),
        }
        link.detected_template_type = template.template_type
        link.classification_confidence = 1.0
        link.classification_status = NdTemplateClassificationStatus.COMPLETED
        link.classified_at = datetime.now(timezone.utc)
        link.classified_by = "user"
        link.metadata_ = metadata
        await self.db.flush()
        await self.log_template_document_classified(link)
        return await self.get_template_document_detail_or_raise(template_id, document_link_id)

    async def add_template_document(
        self,
        template_id: uuid.UUID,
        *,
        current_user: User,
        knowledge_base_source_id: uuid.UUID | None = None,
        document_id: uuid.UUID | None = None,
    ) -> NdControlTemplateDocument:
        template = await self.get_template_or_raise(template_id)
        if knowledge_base_source_id is None and document_id is None:
            raise NdControlTemplateServiceError("Укажите knowledge_base_source_id или document_id")
        if knowledge_base_source_id is not None and document_id is not None:
            raise NdControlTemplateServiceError("Укажите только один идентификатор источника или документа")

        source = (
            await self._source_for_id(knowledge_base_source_id, current_user)
            if knowledge_base_source_id is not None
            else await self._source_for_document(template.id, document_id, current_user)
        )
        if source is None:
            raise NdControlTemplateServiceError("Источник базы знаний не найден")
        await self._ensure_template_kb_link(template.id, source.knowledge_base_id)

        existing = await self.db.scalar(
            select(NdControlTemplateDocument).where(
                NdControlTemplateDocument.template_id == template.id,
                NdControlTemplateDocument.knowledge_base_source_id == source.id,
            )
        )
        if existing is not None:
            existing.classification_status = NdTemplateClassificationStatus.PENDING
            existing.detected_template_type = None
            existing.classification_confidence = None
            existing.classified_at = None
            existing.classified_by = None
            await self.db.flush()
            await self._log_template_document_added(existing, current_user=current_user, existed=True)
            return existing

        link = NdControlTemplateDocument(
            template_id=template.id,
            knowledge_base_id=source.knowledge_base_id,
            knowledge_base_source_id=source.id,
            document_id=source.document_id,
            document_version_id=source.document_version_id,
            classification_status=NdTemplateClassificationStatus.PENDING,
        )
        self.db.add(link)
        await self.db.flush()
        await self._log_template_document_added(link, current_user=current_user, existed=False)
        return link

    async def delete_template_document(self, template_id: uuid.UUID, document_link_id: uuid.UUID, *, current_user: User) -> None:
        await self._require_manage(current_user)
        link = await self.db.get(NdControlTemplateDocument, document_link_id)
        if link is None or link.template_id != template_id:
            raise NdControlTemplateServiceError("Документ шаблона не найден")
        await self.db.delete(link)
        await self.db.flush()

    async def mark_document_classification_processing(self, document_link_id: uuid.UUID) -> None:
        link = await self.db.get(NdControlTemplateDocument, document_link_id)
        if link is None:
            raise NdControlTemplateServiceError("Документ шаблона не найден")
        link.classification_status = NdTemplateClassificationStatus.PROCESSING
        link.classified_at = datetime.now(timezone.utc)
        link.classified_by = "system"
        await self.db.flush()

    async def mark_document_classification_needs_review(self, document_link_id: uuid.UUID) -> None:
        link = await self.db.get(NdControlTemplateDocument, document_link_id)
        if link is None:
            raise NdControlTemplateServiceError("Документ шаблона не найден")
        link.classification_status = NdTemplateClassificationStatus.NEEDS_REVIEW
        link.classified_at = datetime.now(timezone.utc)
        link.classified_by = "system"
        await self.db.flush()

    async def log_template_document_classified(self, link: NdControlTemplateDocument) -> None:
        document = await self.db.get(Document, link.document_id)
        await NdChangeJournalService(self.db).log_event(
            event_type=NdChangeJournalEventType.TEMPLATE_DOCUMENT_CLASSIFIED,
            actor_user_id=None,
            resource_type="nd_control_template_document",
            resource_id=link.id,
            template_id=link.template_id,
            document_id=link.document_id,
            document_name=(document.title or document.original_filename) if document else None,
            summary=(
                "Документ шаблона классифицирован: "
                f"{getattr(link.detected_template_type, 'value', link.detected_template_type) or 'не определено'}"
            ),
            source=NdChangeJournalSource.SYSTEM,
            payload={
                "detected_template_type": getattr(link.detected_template_type, "value", link.detected_template_type),
                "classification_confidence": link.classification_confidence,
                "classification_status": getattr(link.classification_status, "value", link.classification_status),
                "metadata": link.metadata_,
            },
        )

    async def archive_template(self, template_id: uuid.UUID, *, current_user: User) -> None:
        await self._require_manage(current_user)
        template = await self.get_template_or_raise(template_id)
        template.is_active = False
        await self.db.flush()

    async def get_template_or_raise(self, template_id: uuid.UUID) -> NdControlTemplate:
        template = await self.db.get(NdControlTemplate, template_id)
        if template is None:
            raise NdControlTemplateServiceError("Шаблон не найден")
        return template

    async def get_template_detail_or_raise(self, template_id: uuid.UUID) -> dict:
        template = await self.get_template_or_raise(template_id)
        kb_count = int(
            await self.db.scalar(
                select(func.count())
                .select_from(NdControlTemplateKnowledgeBase)
                .where(NdControlTemplateKnowledgeBase.template_id == template.id)
            )
            or 0
        )
        doc_count = int(
            await self.db.scalar(
                select(func.count())
                .select_from(NdControlTemplateDocument)
                .where(NdControlTemplateDocument.template_id == template.id)
            )
            or 0
        )
        kb_ids_result = await self.db.execute(
            select(NdControlTemplateKnowledgeBase.knowledge_base_id).where(
                NdControlTemplateKnowledgeBase.template_id == template.id
            )
        )
        row = await self._template_row(template, kb_count, doc_count)
        row["knowledge_base_ids"] = list(kb_ids_result.scalars().all())
        return row

    async def _require_manage(self, current_user: User) -> None:
        if not await can_manage_nd_control_templates(self.db, current_user):
            raise NdControlTemplateServiceError("Недостаточно прав для управления шаблонами")

    async def _validate_kb_access(self, user: User, knowledge_base_ids: list[uuid.UUID]) -> None:
        for kb_id in knowledge_base_ids:
            kb = await self.db.get(KnowledgeBase, kb_id)
            if kb is None or kb.deleted_at is not None:
                raise NdControlTemplateServiceError(f"База знаний {kb_id} не найдена")
            access = await self.kb_access.can_access_knowledge_base(
                user=user,
                knowledge_base=kb,
                required_access=KnowledgeBaseAccessType.READ,
            )
            if not access.allowed:
                raise NdControlTemplateServiceError(f"Нет доступа к базе знаний «{kb.name}»")

    async def _ensure_template_kb_link(self, template_id: uuid.UUID, knowledge_base_id: uuid.UUID) -> None:
        existing = await self.db.scalar(
            select(NdControlTemplateKnowledgeBase).where(
                NdControlTemplateKnowledgeBase.template_id == template_id,
                NdControlTemplateKnowledgeBase.knowledge_base_id == knowledge_base_id,
            )
        )
        if existing is not None:
            return
        self.db.add(
            NdControlTemplateKnowledgeBase(
                template_id=template_id,
                knowledge_base_id=knowledge_base_id,
            )
        )
        await self.db.flush()

    async def _log_template_document_added(
        self,
        link: NdControlTemplateDocument,
        *,
        current_user: User,
        existed: bool,
    ) -> None:
        document = await self.db.get(Document, link.document_id)
        await NdChangeJournalService(self.db).log_event(
            event_type=NdChangeJournalEventType.TEMPLATE_DOCUMENT_ADDED,
            actor_user_id=current_user.id,
            resource_type="nd_control_template_document",
            resource_id=link.id,
            template_id=link.template_id,
            document_id=link.document_id,
            document_name=(document.title or document.original_filename) if document else None,
            summary=(
                "Документ добавлен в шаблон"
                if not existed
                else "Документ шаблона повторно поставлен на классификацию"
            ),
            source=NdChangeJournalSource.MANUAL,
            payload={
                "knowledge_base_id": str(link.knowledge_base_id),
                "knowledge_base_source_id": str(link.knowledge_base_source_id),
                "document_version_id": str(link.document_version_id),
                "existed": existed,
            },
        )

    async def _source_for_id(
        self,
        source_id: uuid.UUID,
        current_user: User,
    ) -> KnowledgeBaseSource | None:
        result = await self.db.execute(
            select(KnowledgeBaseSource, KnowledgeBase)
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeBaseSource.knowledge_base_id)
            .where(KnowledgeBaseSource.id == source_id, KnowledgeBase.deleted_at.is_(None))
        )
        row = result.one_or_none()
        if row is None:
            return None
        source, kb = row
        access = await self.kb_access.can_access_knowledge_base(
            user=current_user,
            knowledge_base=kb,
            required_access=KnowledgeBaseAccessType.READ,
        )
        if not access.allowed:
            raise NdControlTemplateServiceError(f"Нет доступа к базе знаний «{kb.name}»")
        return source

    async def _source_for_document(
        self,
        template_id: uuid.UUID,
        document_id: uuid.UUID,
        current_user: User,
    ) -> KnowledgeBaseSource | None:
        linked_kbs = (
            select(NdControlTemplateKnowledgeBase.knowledge_base_id)
            .where(NdControlTemplateKnowledgeBase.template_id == template_id)
            .subquery()
        )
        result = await self.db.execute(
            select(KnowledgeBaseSource, KnowledgeBase)
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeBaseSource.knowledge_base_id)
            .where(
                KnowledgeBaseSource.document_id == document_id,
                KnowledgeBaseSource.knowledge_base_id.in_(select(linked_kbs.c.knowledge_base_id)),
                KnowledgeBase.deleted_at.is_(None),
            )
            .order_by(KnowledgeBaseSource.created_at.desc())
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None
        source, kb = row
        access = await self.kb_access.can_access_knowledge_base(
            user=current_user,
            knowledge_base=kb,
            required_access=KnowledgeBaseAccessType.READ,
        )
        if not access.allowed:
            raise NdControlTemplateServiceError(f"Нет доступа к базе знаний «{kb.name}»")
        return source

    async def _template_row(
        self,
        template: NdControlTemplate,
        knowledge_bases_count: int = 0,
        documents_count: int = 0,
    ) -> dict:
        stats = await self._classification_stats(template.id)
        label = ND_TEMPLATE_TYPE_LABELS[template.template_type]
        return {
            "id": template.id,
            "template_type": template.template_type,
            "template_type_label": label,
            "name": template.name,
            "title": template.name,
            "description": template.description,
            "sort_order": template.sort_order,
            "is_active": template.is_active,
            "created_by_user_id": template.created_by_user_id,
            "knowledge_bases_count": knowledge_bases_count,
            "documents_count": documents_count,
            "classification_stats": stats,
            "created_at": template.created_at,
            "updated_at": template.updated_at,
        }

    async def _classification_stats(self, template_id: uuid.UUID) -> dict:
        result = await self.db.execute(
            select(
                NdControlTemplateDocument.classification_status,
                func.count(NdControlTemplateDocument.id),
            )
            .where(NdControlTemplateDocument.template_id == template_id)
            .group_by(NdControlTemplateDocument.classification_status)
        )
        stats = {status.value: 0 for status in NdTemplateClassificationStatus}
        for status, count in result.all():
            stats[getattr(status, "value", status)] = int(count or 0)
        return stats

    @staticmethod
    def _document_row(
        link: NdControlTemplateDocument,
        kb: KnowledgeBase,
        document: Document,
    ) -> dict:
        detected_label = get_template_type_label(link.detected_template_type)
        return {
            "id": link.id,
            "template_id": link.template_id,
            "knowledge_base_id": link.knowledge_base_id,
            "knowledge_base_source_id": link.knowledge_base_source_id,
            "document_id": link.document_id,
            "document_version_id": link.document_version_id,
            "detected_template_type": link.detected_template_type,
            "detected_template_type_label": detected_label,
            "classification_confidence": link.classification_confidence,
            "classification_status": link.classification_status,
            "classified_at": link.classified_at,
            "classified_by": link.classified_by,
            "metadata": link.metadata_,
            "knowledge_base_name": kb.name,
            "document_title": document.title,
            "original_filename": document.original_filename,
            "created_at": link.created_at,
            "updated_at": link.updated_at,
        }

    @staticmethod
    def _default_sort_order(template_type: NdTemplateType) -> int:
        return list(ND_TEMPLATE_TYPE_LABELS).index(template_type) * 10
