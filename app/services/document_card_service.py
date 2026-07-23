from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_card import QmsDocumentCard
from app.models.enums import DocumentCardStatus
from app.schemas.common import Page
from app.schemas.document_card import DocumentCardBootstrapResult, DocumentCardCreate, DocumentCardUpdate
from app.services.document_card_utils import (
    extract_document_code,
    fallback_document_code,
    infer_document_kind,
    infer_qms_level,
)


class DocumentCardServiceError(ValueError):
    pass


class DocumentCardService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(
        self,
        *,
        query: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> Page[QmsDocumentCard]:
        filters = []
        if query:
            needle = f"%{query.strip()}%"
            filters.append(
                or_(
                    QmsDocumentCard.document_code.ilike(needle),
                    QmsDocumentCard.document_name.ilike(needle),
                    QmsDocumentCard.owner_department.ilike(needle),
                    QmsDocumentCard.original_storage_location.ilike(needle),
                )
            )
        count_stmt = select(func.count()).select_from(QmsDocumentCard)
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = await self.db.scalar(count_stmt)

        stmt = select(QmsDocumentCard)
        if filters:
            stmt = stmt.where(*filters)
        stmt = stmt.order_by(QmsDocumentCard.document_code.asc()).offset(max(page - 1, 0) * size).limit(size)
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())
        return Page(items=items, total=int(total or 0), page=page, size=size)

    async def list_all(self) -> list[QmsDocumentCard]:
        result = await self.db.execute(select(QmsDocumentCard).order_by(QmsDocumentCard.document_code.asc()))
        return list(result.scalars().all())

    async def get_or_raise(self, card_id: uuid.UUID) -> QmsDocumentCard:
        card = await self.db.get(QmsDocumentCard, card_id)
        if card is None:
            raise DocumentCardServiceError("Карточка документа не найдена")
        return card

    async def get_by_document_id(self, document_id: uuid.UUID) -> QmsDocumentCard | None:
        result = await self.db.execute(
            select(QmsDocumentCard).where(QmsDocumentCard.document_id == document_id)
        )
        return result.scalar_one_or_none()

    async def get_by_code(self, document_code: str) -> QmsDocumentCard | None:
        result = await self.db.execute(
            select(QmsDocumentCard).where(QmsDocumentCard.document_code == document_code.strip().upper())
        )
        return result.scalar_one_or_none()

    async def create(self, payload: DocumentCardCreate) -> QmsDocumentCard:
        existing = await self.get_by_document_id(payload.document_id)
        if existing is not None:
            raise DocumentCardServiceError("Карточка для этого документа уже существует")
        duplicate_code = await self.get_by_code(payload.document_code)
        if duplicate_code is not None:
            raise DocumentCardServiceError("Карточка с таким кодом документа уже существует")
        document = await self.db.get(Document, payload.document_id)
        if document is None:
            raise DocumentCardServiceError("Документ не найден")

        card = QmsDocumentCard(
            document_id=payload.document_id,
            document_code=payload.document_code.strip().upper(),
            document_name=payload.document_name,
            document_type=payload.document_type,
            qms_level=payload.qms_level,
            version=payload.version,
            status=payload.status,
            approval_date=payload.approval_date,
            effective_date=payload.effective_date,
            process_owner=payload.process_owner,
            author=payload.author,
            reviewer=payload.reviewer,
            approver=payload.approver,
            owner_department=payload.owner_department,
            scope=payload.scope,
            related_processes=payload.related_processes,
            related_departments=payload.related_departments,
            related_documents=payload.related_documents,
            normative_references=payload.normative_references,
            record_forms=payload.record_forms,
            retention_period=payload.retention_period,
            original_storage_location=payload.original_storage_location,
            electronic_storage_location=payload.electronic_storage_location,
            has_process_diagram=payload.has_process_diagram,
            has_acknowledgement_sheet=payload.has_acknowledgement_sheet,
            acknowledgement_targets=payload.acknowledgement_targets,
            confidentiality_level=payload.confidentiality_level,
            change_history=payload.change_history,
            approval_history=payload.approval_history,
            attachments=payload.attachments,
            archived_versions=payload.archived_versions,
        )
        self.db.add(card)
        await self.db.flush()
        return card

    async def create_from_document(self, document: Document) -> QmsDocumentCard:
        existing = await self.get_by_document_id(document.id)
        if existing is not None:
            return existing

        metadata = document.metadata_ or document.doc_metadata or {}
        code = extract_document_code(
            title=document.title,
            original_filename=document.original_filename,
            metadata=metadata,
        ) or fallback_document_code(str(document.id))
        document_kind = infer_document_kind(code)
        attachments: list[str] = []
        if document.original_filename:
            attachments.append(document.original_filename)

        payload = DocumentCardCreate(
            document_id=document.id,
            document_code=code,
            document_name=str(metadata.get("document_name") or document.title),
            document_type=document_kind,
            qms_level=infer_qms_level(document_kind),
            version=str(metadata.get("version") or metadata.get("version_label") or ""),
            status=DocumentCardStatus.DRAFT,
            process_owner=metadata.get("process_owner"),
            author=metadata.get("author"),
            reviewer=metadata.get("reviewer"),
            approver=metadata.get("approver"),
            owner_department=metadata.get("owner_department"),
            scope=metadata.get("scope"),
            related_processes=metadata.get("related_processes") or [],
            related_departments=metadata.get("related_departments") or [],
            related_documents=metadata.get("related_documents") or [],
            normative_references=metadata.get("normative_references") or [],
            record_forms=metadata.get("record_forms") or [],
            retention_period=metadata.get("retention_period"),
            original_storage_location=metadata.get("original_storage_location"),
            electronic_storage_location=metadata.get("electronic_storage_location") or "DMS/Knowledge Base",
            has_process_diagram=bool(metadata.get("has_process_diagram")),
            has_acknowledgement_sheet=bool(metadata.get("has_acknowledgement_sheet")),
            acknowledgement_targets=metadata.get("acknowledgement_targets") or [],
            attachments=attachments,
            archived_versions=metadata.get("archived_versions") or [],
            change_history=metadata.get("change_history") or [],
            approval_history=metadata.get("approval_history") or [],
        )
        return await self.create(payload)

    async def bootstrap_for_all_documents(self) -> DocumentCardBootstrapResult:
        result = await self.db.execute(select(Document).order_by(Document.created_at.asc()))
        documents = list(result.scalars().all())
        created = 0
        skipped = 0
        for document in documents:
            existing = await self.get_by_document_id(document.id)
            if existing is not None:
                skipped += 1
                continue
            await self.create_from_document(document)
            created += 1
        return DocumentCardBootstrapResult(
            created=created,
            skipped=skipped,
            total_documents=len(documents),
        )

    async def update(self, card_id: uuid.UUID, payload: DocumentCardUpdate) -> QmsDocumentCard:
        card = await self.get_or_raise(card_id)
        updates = payload.model_dump(exclude_unset=True)
        if "document_code" in updates and updates["document_code"] is not None:
            updates["document_code"] = updates["document_code"].strip().upper()
            duplicate = await self.get_by_code(updates["document_code"])
            if duplicate is not None and duplicate.id != card.id:
                raise DocumentCardServiceError("Карточка с таким кодом документа уже существует")
        for field, value in updates.items():
            setattr(card, field, value)
        await self.db.flush()
        return card
