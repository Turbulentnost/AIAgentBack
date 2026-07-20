from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.agents.nd_control_agent.knowledge_base_access_schemas import (
    KnowledgeBaseDocumentChunk,
    KnowledgeBaseDocumentListItem,
    KnowledgeBaseDocumentMetadata,
    KnowledgeBaseDocumentTextResult,
    KnowledgeBaseSearchFragment,
    KnowledgeBaseSearchResult,
)
from app.core.logging import get_logger
from app.documents.chunk_utils import chunk_display_text
from app.documents.storage import ObjectStorageError, object_storage
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.knowledge_base import KnowledgeBaseSource
from app.models.user import User
from app.services.knowledge_base_search_service import KnowledgeBaseSearchService
from app.services.knowledge_base_service import KnowledgeBaseService, KnowledgeBaseServiceError

logger = get_logger(__name__)


class KnowledgeBaseAccessServiceError(Exception):
    def __init__(self, message: str, *, code: str = "error") -> None:
        self.message = message
        self.code = code
        super().__init__(message)


class KnowledgeBaseAccessService:
    """Обёртка над модулем баз знаний для nd_control_agent."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._kb_service = KnowledgeBaseService(db)
        self._search_service = KnowledgeBaseSearchService(db)

    async def list_documents(self, knowledge_base_id: str) -> list[KnowledgeBaseDocumentListItem]:
        kb_id = self._parse_uuid(knowledge_base_id, field="knowledge_base_id")
        logger.info("nd_control.kb_access.list_documents", knowledge_base_id=str(kb_id))
        try:
            await self._kb_service.get_or_raise(kb_id)
        except KnowledgeBaseServiceError as exc:
            raise KnowledgeBaseAccessServiceError(str(exc), code="knowledge_base_not_found") from exc

        result = await self.db.execute(
            select(KnowledgeBaseSource, Document)
            .join(Document, Document.id == KnowledgeBaseSource.document_id)
            .where(KnowledgeBaseSource.knowledge_base_id == kb_id)
            .order_by(KnowledgeBaseSource.added_at.desc())
        )
        items: list[KnowledgeBaseDocumentListItem] = []
        for source, document in result.all():
            items.append(
                KnowledgeBaseDocumentListItem(
                    document_id=document.id,
                    knowledge_base_id=kb_id,
                    file_name=document.original_filename,
                    title=document.title,
                    parse_status=_enum_value(source.processing_status),
                    created_at=source.created_at or document.created_at,
                    updated_at=source.updated_at or document.updated_at,
                )
            )
        logger.info(
            "nd_control.kb_access.list_documents.done",
            knowledge_base_id=str(kb_id),
            count=len(items),
        )
        return items

    async def get_document_metadata(self, document_id: str) -> KnowledgeBaseDocumentMetadata:
        doc_id = self._parse_uuid(document_id, field="document_id")
        logger.info("nd_control.kb_access.get_document_metadata", document_id=str(doc_id))
        document = await self._get_document_or_raise(doc_id)
        source = await self._find_kb_source_for_document(doc_id)
        parse_status = _enum_value(source.processing_status) if source else _enum_value(document.text_extract_status)
        return KnowledgeBaseDocumentMetadata(
            document_id=document.id,
            knowledge_base_id=source.knowledge_base_id if source else None,
            file_name=document.original_filename,
            title=document.title,
            parse_status=parse_status,
            size=document.file_size,
            created_at=document.created_at,
            updated_at=document.updated_at,
            extra={
                "content_type": document.content_type,
                "mime_type": document.mime_type,
                "document_type": _enum_value(document.document_type),
                "processing_status": _enum_value(document.processing_status),
                "text_extract_status": _enum_value(document.text_extract_status),
                "pages_count": document.pages_count,
                "is_indexed": document.is_indexed,
                "is_knowledge_base": document.is_knowledge_base,
                "department_id": str(document.department_id) if document.department_id else None,
                "metadata": document.metadata_ or document.doc_metadata or {},
            },
        )

    async def get_document_chunks(self, document_id: str) -> list[KnowledgeBaseDocumentChunk]:
        doc_id = self._parse_uuid(document_id, field="document_id")
        logger.info("nd_control.kb_access.get_document_chunks", document_id=str(doc_id))
        await self._get_document_or_raise(doc_id)
        version = await self._resolve_document_version(doc_id)
        if version is None:
            raise KnowledgeBaseAccessServiceError("У документа нет версий", code="document_not_found")

        result = await self.db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == version.id)
            .order_by(DocumentChunk.chunk_index.asc())
        )
        chunks = list(result.scalars().all())
        if not chunks:
            raise KnowledgeBaseAccessServiceError(
                "Чанки документа не найдены",
                code="chunks_not_found",
            )

        items = [
            KnowledgeBaseDocumentChunk(
                chunk_id=chunk.id,
                document_id=doc_id,
                text=chunk_display_text(chunk),
                page_number=chunk.page_number,
                section=chunk.section_title,
                metadata={
                    **(chunk.metadata_ or {}),
                    **(chunk.chunk_metadata or {}),
                    "chunk_index": chunk.chunk_index,
                    "document_version_id": str(chunk.document_version_id),
                    "token_count": chunk.token_count,
                },
            )
            for chunk in chunks
        ]
        logger.info(
            "nd_control.kb_access.get_document_chunks.done",
            document_id=str(doc_id),
            count=len(items),
        )
        return items

    async def get_document_text(self, document_id: str) -> KnowledgeBaseDocumentTextResult:
        doc_id = self._parse_uuid(document_id, field="document_id")
        logger.info("nd_control.kb_access.get_document_text", document_id=str(doc_id))
        document = await self._get_document_or_raise(doc_id)
        version = await self._resolve_document_version(doc_id)
        if version is None:
            return KnowledgeBaseDocumentTextResult(
                document_id=doc_id,
                text="",
                status="empty",
                source="none",
                message="У документа нет версий для извлечения текста",
            )

        extracted_text = self._load_extracted_text(document, version)
        if extracted_text.strip():
            logger.info(
                "nd_control.kb_access.get_document_text.done",
                document_id=str(doc_id),
                source="extracted_text",
                length=len(extracted_text),
            )
            return KnowledgeBaseDocumentTextResult(
                document_id=doc_id,
                text=extracted_text,
                status="ok",
                source="extracted_text",
            )

        assembled_text = await self._assemble_text_from_chunks(version.id)
        if assembled_text.strip():
            logger.info(
                "nd_control.kb_access.get_document_text.done",
                document_id=str(doc_id),
                source="chunks",
                length=len(assembled_text),
            )
            return KnowledgeBaseDocumentTextResult(
                document_id=doc_id,
                text=assembled_text,
                status="ok",
                source="chunks",
            )

        logger.warning("nd_control.kb_access.get_document_text.empty", document_id=str(doc_id))
        return KnowledgeBaseDocumentTextResult(
            document_id=doc_id,
            text="",
            status="empty",
            source="none",
            message="Текст документа недоступен: нет сохранённого извлечённого текста и чанков",
        )

    async def search_in_knowledge_base(
        self,
        knowledge_base_id: str,
        query: str,
        filters: dict | None = None,
        *,
        user: User,
        top_k: int = 5,
        agent_id: uuid.UUID | None = None,
    ) -> KnowledgeBaseSearchResult:
        kb_id = self._parse_uuid(knowledge_base_id, field="knowledge_base_id")
        normalized_query = (query or "").strip()
        if not normalized_query:
            return KnowledgeBaseSearchResult(
                knowledge_base_id=kb_id,
                query=query,
                status="empty",
                message="Поисковый запрос пустой",
                fragments=[],
            )

        logger.info(
            "nd_control.kb_access.search",
            knowledge_base_id=str(kb_id),
            query=normalized_query,
            filters=filters or {},
        )
        try:
            kb = await self._kb_service.get_or_raise(kb_id)
        except KnowledgeBaseServiceError as exc:
            raise KnowledgeBaseAccessServiceError(str(exc), code="knowledge_base_not_found") from exc

        effective_top_k = int((filters or {}).get("top_k", top_k))
        response = await self._search_service.search(
            knowledge_base=kb,
            query=normalized_query,
            user=user,
            top_k=effective_top_k,
            agent_id=agent_id,
            include_inaccessible=False,
        )
        fragments: list[KnowledgeBaseSearchFragment] = []
        for hit in response.hits:
            if not _passes_filters(hit, filters):
                continue
            fragments.append(
                KnowledgeBaseSearchFragment(
                    fragment_id=hit.knowledge_base_chunk_id,
                    document_id=hit.document_id,
                    document_title=hit.document_title,
                    text=hit.content,
                    score=hit.score,
                    page_number=hit.page_number,
                    section=hit.section_title,
                    metadata=hit.metadata or {},
                )
            )

        if not fragments:
            logger.info(
                "nd_control.kb_access.search.empty",
                knowledge_base_id=str(kb_id),
                query=normalized_query,
            )
            return KnowledgeBaseSearchResult(
                knowledge_base_id=kb_id,
                query=normalized_query,
                status="empty",
                message="RAG-поиск не вернул результатов по запросу",
                fragments=[],
            )

        logger.info(
            "nd_control.kb_access.search.done",
            knowledge_base_id=str(kb_id),
            query=normalized_query,
            count=len(fragments),
        )
        return KnowledgeBaseSearchResult(
            knowledge_base_id=kb_id,
            query=normalized_query,
            status="ok",
            fragments=fragments,
        )

    async def _get_document_or_raise(self, document_id: uuid.UUID) -> Document:
        document = await self.db.get(Document, document_id)
        if document is None:
            raise KnowledgeBaseAccessServiceError("Документ не найден", code="document_not_found")
        return document

    async def _find_kb_source_for_document(self, document_id: uuid.UUID) -> KnowledgeBaseSource | None:
        result = await self.db.execute(
            select(KnowledgeBaseSource)
            .where(KnowledgeBaseSource.document_id == document_id)
            .order_by(KnowledgeBaseSource.added_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _resolve_document_version(self, document_id: uuid.UUID) -> DocumentVersion | None:
        source = await self._find_kb_source_for_document(document_id)
        if source is not None:
            version = await self.db.get(DocumentVersion, source.document_version_id)
            if version is not None:
                return version

        result = await self.db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.is_current.desc(), DocumentVersion.version_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    def _load_extracted_text(self, document: Document, version: DocumentVersion) -> str:
        object_name = version.extracted_text_object_name or document.extracted_text_object_name
        if not object_name:
            return ""
        try:
            payload = json.loads(object_storage.get_object(object_name).decode("utf-8"))
        except ObjectStorageError as exc:
            logger.warning(
                "nd_control.kb_access.extracted_text_storage_error",
                document_id=str(document.id),
                object_name=object_name,
                error=str(exc),
            )
            return ""
        except json.JSONDecodeError:
            logger.warning(
                "nd_control.kb_access.extracted_text_invalid_json",
                document_id=str(document.id),
                object_name=object_name,
            )
            return ""
        if not isinstance(payload, dict):
            return ""
        text = payload.get("text")
        return text.strip() if isinstance(text, str) else ""

    async def _assemble_text_from_chunks(self, document_version_id: uuid.UUID) -> str:
        result = await self.db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == document_version_id)
            .order_by(DocumentChunk.chunk_index.asc())
        )
        parts = [chunk_display_text(chunk) for chunk in result.scalars().all()]
        parts = [part for part in parts if part]
        return "\n\n".join(parts)

    @staticmethod
    def _parse_uuid(value: str, *, field: str) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise KnowledgeBaseAccessServiceError(
                f"Некорректный идентификатор {field}",
                code="invalid_id",
            ) from exc


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", str(value))


def _passes_filters(hit: Any, filters: dict | None) -> bool:
    if not filters:
        return True
    if document_id := filters.get("document_id"):
        if hit.document_id is None or str(hit.document_id) != str(document_id):
            return False
    if document_version_id := filters.get("document_version_id"):
        metadata = hit.metadata or {}
        hit_version = metadata.get("document_version_id")
        if hit_version is None or str(hit_version) != str(document_version_id):
            return False
    if page_number := filters.get("page_number"):
        if hit.page_number != page_number:
            return False
    if min_score := filters.get("min_score"):
        if hit.score < float(min_score):
            return False
    return True
