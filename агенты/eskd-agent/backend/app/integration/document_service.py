from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import IntegrationDocument
from app.schemas.integration import UnifiedDocumentCard


class DocumentService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def upsert(self, card: UnifiedDocumentCard) -> IntegrationDocument:
        revision = card.revision or ""
        existing = await self._db.scalar(
            select(IntegrationDocument).where(
                IntegrationDocument.source_system == card.source_system,
                IntegrationDocument.external_document_id == card.document_id,
                IntegrationDocument.revision == revision,
            )
        )
        if existing:
            if card.checksum and existing.checksum and card.checksum != existing.checksum:
                await self._invalidate_jobs_for_document(existing.id)
            existing.designation = card.designation or existing.designation
            existing.document_type = card.document_type or existing.document_type
            existing.name = card.name or existing.name
            existing.sheet_count = card.sheet_count or existing.sheet_count
            existing.author = card.author or existing.author
            existing.department = card.department or existing.department
            existing.product_id = card.product_id or existing.product_id
            existing.checksum = card.checksum or existing.checksum
            existing.files = card.files or existing.files
            existing.related_documents = card.related_documents or existing.related_documents
            existing.route_status = card.status or existing.route_status
            existing.submitted_at = card.submitted_at or existing.submitted_at
            existing.metadata_extra = card.metadata_extra or existing.metadata_extra
            await self._db.commit()
            await self._db.refresh(existing)
            return existing

        row = IntegrationDocument(
            external_document_id=card.document_id,
            source_system=card.source_system,
            designation=card.designation,
            document_type=card.document_type,
            name=card.name,
            revision=revision or None,
            sheet_count=card.sheet_count,
            author=card.author,
            department=card.department,
            product_id=card.product_id,
            checksum=card.checksum,
            files=card.files,
            related_documents=card.related_documents,
            route_status=card.status,
            submitted_at=card.submitted_at or datetime.now(timezone.utc),
            metadata_extra=card.metadata_extra,
        )
        self._db.add(row)
        await self._db.commit()
        await self._db.refresh(row)
        return row

    async def _invalidate_jobs_for_document(self, document_id) -> None:
        from app.models.integration import IntegrationJob

        await self._db.execute(
            update(IntegrationJob)
            .where(IntegrationJob.document_id == document_id, IntegrationJob.is_stale.is_(False))
            .values(is_stale=True)
        )
        await self._db.commit()

    async def get_by_external(
        self,
        *,
        source_system: str,
        external_document_id: str,
        revision: str | None = None,
    ) -> IntegrationDocument | None:
        query = select(IntegrationDocument).where(
            IntegrationDocument.source_system == source_system,
            IntegrationDocument.external_document_id == external_document_id,
        )
        if revision is not None:
            query = query.where(IntegrationDocument.revision == revision)
        return await self._db.scalar(query.order_by(IntegrationDocument.updated_at.desc()))
