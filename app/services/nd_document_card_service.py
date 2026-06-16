from __future__ import annotations

import re
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.document import Document, DocumentVersion
from app.models.enums import NdDocumentCardStatus
from app.models.knowledge_base import KnowledgeBaseSource
from app.models.nd_control_registry import (
    NdControlDepartment,
    NdControlDepartmentKnowledgeBase,
    NdDocumentCard,
)

_DOCUMENT_CODE_RE = re.compile(
    r"(?:СТО|STO|И|РГ|РИ|ПЛ|ДИ|ПП|Регламент)[-\s]?[\d]+(?:[-\.][\d\w]+)*",
    re.IGNORECASE,
)


class NdDocumentCardServiceError(Exception):
    pass


def guess_document_code_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    match = _DOCUMENT_CODE_RE.search(filename)
    return match.group(0).strip() if match else None


class NdDocumentCardService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_cards(
        self,
        *,
        department_id: uuid.UUID | None = None,
        knowledge_base_id: uuid.UUID | None = None,
        query: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[NdDocumentCard], int]:
        stmt = select(NdDocumentCard).where(NdDocumentCard.status != NdDocumentCardStatus.ARCHIVED)
        if department_id is not None:
            stmt = stmt.where(NdDocumentCard.department_id == department_id)
        if knowledge_base_id is not None:
            stmt = stmt.where(NdDocumentCard.knowledge_base_id == knowledge_base_id)
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(
                    NdDocumentCard.document_code.ilike(pattern),
                    NdDocumentCard.document_name.ilike(pattern),
                )
            )
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int(await self.db.scalar(count_stmt) or 0)
        offset = max(0, (page - 1) * size)
        result = await self.db.execute(
            stmt.order_by(NdDocumentCard.document_code.nulls_last(), NdDocumentCard.document_name)
            .offset(offset)
            .limit(size)
        )
        return list(result.scalars().all()), total

    async def get_card(self, card_id: uuid.UUID) -> NdDocumentCard | None:
        return await self.db.get(NdDocumentCard, card_id)

    async def get_card_or_raise(self, card_id: uuid.UUID) -> NdDocumentCard:
        card = await self.get_card(card_id)
        if card is None:
            raise NdDocumentCardServiceError("Карточка документа не найдена")
        return card

    async def update_card(self, card_id: uuid.UUID, payload: dict) -> NdDocumentCard:
        card = await self.get_card_or_raise(card_id)
        for key, value in payload.items():
            if hasattr(card, key):
                setattr(card, key, value)
        await self.db.flush()
        return card

    async def archive_card_for_source(self, source_id: uuid.UUID) -> None:
        card = await self.db.scalar(
            select(NdDocumentCard).where(NdDocumentCard.knowledge_base_source_id == source_id)
        )
        if card is not None:
            card.status = NdDocumentCardStatus.ARCHIVED
            await self.db.flush()

    async def ensure_card_for_source(
        self,
        source: KnowledgeBaseSource,
        *,
        department: NdControlDepartment | None = None,
    ) -> NdDocumentCard | None:
        existing = await self.db.scalar(
            select(NdDocumentCard).where(NdDocumentCard.knowledge_base_source_id == source.id)
        )
        if existing is not None:
            if existing.status == NdDocumentCardStatus.ARCHIVED:
                existing.status = NdDocumentCardStatus.DRAFT
                await self.db.flush()
            return existing

        if department is None:
            link = await self.db.scalar(
                select(NdControlDepartmentKnowledgeBase)
                .join(NdControlDepartment, NdControlDepartment.id == NdControlDepartmentKnowledgeBase.department_id)
                .where(
                    NdControlDepartmentKnowledgeBase.knowledge_base_id == source.knowledge_base_id,
                    NdControlDepartment.is_active.is_(True),
                )
                .options(selectinload(NdControlDepartmentKnowledgeBase.department))
            )
            if link is None:
                return None
            department = link.department

        document = await self.db.get(Document, source.document_id)
        version = await self.db.get(DocumentVersion, source.document_version_id)
        filename = (
            (document.original_filename if document else None)
            or (version.original_filename if version else None)
            or ""
        )
        title = (document.title if document and document.title else None) or filename

        card = NdDocumentCard(
            department_id=department.id,
            knowledge_base_id=source.knowledge_base_id,
            knowledge_base_source_id=source.id,
            document_id=source.document_id,
            document_version_id=source.document_version_id,
            document_code=guess_document_code_from_filename(filename),
            document_name=title or None,
            status=NdDocumentCardStatus.DRAFT,
            attachments=[filename] if filename else [],
            electronic_storage_location="DMS/Knowledge Base",
            related_processes=[],
            related_departments=[],
            related_documents=[],
            normative_references=[],
            record_forms=[],
            acknowledgement_targets=[],
            change_history=[],
            approval_history=[],
            archived_versions=[],
        )
        self.db.add(card)
        await self.db.flush()
        return card

    async def backfill_cards_for_department_kb(
        self,
        department: NdControlDepartment,
        knowledge_base_id: uuid.UUID,
    ) -> int:
        result = await self.db.execute(
            select(KnowledgeBaseSource).where(KnowledgeBaseSource.knowledge_base_id == knowledge_base_id)
        )
        created = 0
        for source in result.scalars().all():
            before = await self.db.scalar(
                select(NdDocumentCard.id).where(NdDocumentCard.knowledge_base_source_id == source.id)
            )
            card = await self.ensure_card_for_source(source, department=department)
            if card is not None and before is None:
                created += 1
        return created
