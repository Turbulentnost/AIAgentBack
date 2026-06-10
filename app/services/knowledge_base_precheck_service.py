from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document import Document, DocumentVersion
from app.models.enums import (
    DocumentProcessingStatus,
    KnowledgeBaseIndexErrorType,
    KnowledgeBaseSourcePrecheckStatus,
    KnowledgeBaseSourceStatus,
)
from app.models.knowledge_base import KnowledgeBaseSource
from app.models.user import User
from app.services.permission_service import PermissionService


@dataclass
class PrecheckResult:
    passed: bool
    error_type: KnowledgeBaseIndexErrorType | None = None
    user_message: str | None = None
    technical_message: str | None = None
    recommended_action: str | None = None
    needs_ocr: bool = False


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_CONTENT_PREFIXES = (
    "application/pdf",
    "application/vnd.openxmlformats",
    "application/msword",
    "application/vnd.ms-excel",
    "text/",
    "image/",
)


class KnowledgeBasePrecheckService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.permissions = PermissionService(db)

    async def precheck_source(
        self,
        source: KnowledgeBaseSource,
        *,
        user: User | None = None,
    ) -> PrecheckResult:
        document = await self.db.get(Document, source.document_id)
        version = await self.db.get(DocumentVersion, source.document_version_id)
        if document is None or version is None:
            return PrecheckResult(
                passed=False,
                error_type=KnowledgeBaseIndexErrorType.DAMAGED_FILE,
                user_message="Документ или версия не найдены.",
                technical_message="Document or DocumentVersion missing",
                recommended_action="Проверьте, что документ не удалён.",
            )

        if user is not None and not user.is_superuser:
            if not await self.permissions.can_access_document(user, document.id):
                return PrecheckResult(
                    passed=False,
                    error_type=KnowledgeBaseIndexErrorType.DOCUMENT_ACCESS_DENIED,
                    user_message="Нет прав на использование этого документа.",
                    technical_message=f"User {user.id} cannot access document {document.id}",
                    recommended_action="Запросите доступ к документу или выберите другой источник.",
                )

        content_type = (document.content_type or document.mime_type or "").lower()
        filename = (document.original_filename or version.original_filename or "").lower()
        if not self._is_supported_format(content_type, filename):
            return PrecheckResult(
                passed=False,
                error_type=KnowledgeBaseIndexErrorType.UNSUPPORTED_FORMAT,
                user_message="Формат файла не поддерживается для индексации.",
                technical_message=f"content_type={content_type}, filename={filename}",
                recommended_action="Загрузите PDF, DOCX, XLSX или изображение.",
            )

        file_size = source.file_size or version.file_size or document.file_size or 0
        if file_size > settings.DOCUMENT_MAX_UPLOAD_SIZE_BYTES:
            return PrecheckResult(
                passed=False,
                error_type=KnowledgeBaseIndexErrorType.DAMAGED_FILE,
                user_message="Файл превышает допустимый размер.",
                technical_message=f"file_size={file_size}",
                recommended_action="Уменьшите размер файла или разбейте на части.",
            )

        if getattr(document.processing_status, "value", document.processing_status) == DocumentProcessingStatus.FAILED.value:
            return PrecheckResult(
                passed=False,
                error_type=KnowledgeBaseIndexErrorType.TEXT_EXTRACT_FAILED,
                user_message="Документ в статусе ошибки и не может быть проиндексирован.",
                technical_message=f"document {document.id} processing_status=failed",
                recommended_action="Переобработайте документ или выберите другую версию.",
            )

        if not version.is_current:
            return PrecheckResult(
                passed=False,
                error_type=KnowledgeBaseIndexErrorType.VERSION_CONFLICT,
                user_message="Выбрана неактуальная версия документа.",
                technical_message=f"version {version.id} is not current",
                recommended_action="Выберите текущую версию документа.",
            )

        checksum = document.checksum or version.checksum
        if checksum:
            duplicate = await self.db.scalar(
                select(KnowledgeBaseSource.id).where(
                    KnowledgeBaseSource.knowledge_base_id == source.knowledge_base_id,
                    KnowledgeBaseSource.checksum == checksum,
                    KnowledgeBaseSource.id != source.id,
                )
            )
            if duplicate is not None:
                return PrecheckResult(
                    passed=False,
                    error_type=KnowledgeBaseIndexErrorType.VERSION_CONFLICT,
                    user_message="Документ с таким checksum уже добавлен в базу знаний.",
                    technical_message=f"duplicate checksum={checksum}",
                    recommended_action="Удалите дубликат или выберите другую версию.",
                )
            source.checksum = checksum

        needs_ocr = self._likely_needs_ocr(content_type, filename, version)
        if needs_ocr:
            source.processing_status = KnowledgeBaseSourceStatus.NEEDS_OCR
            return PrecheckResult(
                passed=True,
                needs_ocr=True,
                user_message="Документ может потребовать OCR при индексации.",
            )

        source.precheck_status = KnowledgeBaseSourcePrecheckStatus.PASSED
        source.processing_status = KnowledgeBaseSourceStatus.READY_TO_INDEX
        source.precheck_notes = None
        await self.db.flush()
        return PrecheckResult(passed=True)

    def _is_supported_format(self, content_type: str, filename: str) -> bool:
        if any(content_type.startswith(prefix) for prefix in SUPPORTED_CONTENT_PREFIXES):
            return True
        return any(filename.endswith(ext) for ext in SUPPORTED_EXTENSIONS)

    def _likely_needs_ocr(self, content_type: str, filename: str, version: DocumentVersion) -> bool:
        if content_type.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".webp")):
            return True
        metadata = version.metadata_ or {}
        pdf_meta = metadata.get("pdf_parsing") or {}
        if pdf_meta.get("ocr_pages_count", 0) > 0:
            return True
        if "pdf" in content_type or filename.endswith(".pdf"):
            pages = version.pages_count or 0
            if pages > 0 and not version.extracted_text_object_name:
                return True
        return False
