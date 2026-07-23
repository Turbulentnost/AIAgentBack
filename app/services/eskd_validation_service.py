from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.storage import ObjectStorageError, object_storage
from app.eskd.constants import ESKD_METADATA_KEY
from app.eskd.designation import ESKD_DESIGNATION_CHARS_RE, normalize_designation
from app.eskd.validation.engine import EskdValidationEngine
from app.eskd.validation.rules import EskdValidationContext
from app.eskd.validation.schemas import EskdValidationReport
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.document_card import QmsDocumentCard
from app.models.enums import EskdRegistrationStatus
from app.models.eskd_registration import EskdDocumentRegistration
from app.services.eskd_registration_service import EskdRegistrationService, EskdRegistrationServiceError


class EskdValidationServiceError(ValueError):
    pass


class EskdValidationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.engine = EskdValidationEngine()

    async def validate_registration(self, registration_id: uuid.UUID) -> EskdValidationReport:
        registration = await self._load_registration(registration_id)
        return await self._validate_and_persist(registration)

    async def validate_document(self, document_id: uuid.UUID) -> EskdValidationReport:
        registration = await self.db.scalar(
            select(EskdDocumentRegistration).where(EskdDocumentRegistration.document_id == document_id)
        )
        if registration is None:
            raise EskdValidationServiceError("Документ не зарегистрирован в модуле ЕСКД")
        return await self._validate_and_persist(registration)

    async def get_validation_report(self, registration_id: uuid.UUID) -> EskdValidationReport:
        registration = await self._load_registration(registration_id)
        stored = (registration.metadata_ or {}).get("last_validation")
        if not isinstance(stored, dict):
            raise EskdValidationServiceError("Проверка ЕСКД ещё не выполнялась")
        return self._report_from_dict(stored, registration=registration)

    async def _validate_and_persist(self, registration: EskdDocumentRegistration) -> EskdValidationReport:
        document = await self.db.get(Document, registration.document_id)
        if document is None:
            raise EskdValidationServiceError("Связанный документ не найден")

        card_code = None
        if registration.qms_document_card_id:
            card = await self.db.get(QmsDocumentCard, registration.qms_document_card_id)
            card_code = card.document_code if card else None

        document_text = await self._load_document_text(document)
        context = EskdValidationContext(
            designation=registration.designation,
            document_kind=registration.document_kind,
            document_title=document.title,
            original_filename=document.original_filename,
            document_type=document.document_type,
            text_extract_status=document.text_extract_status,
            document_text=document_text,
            qms_document_code=card_code,
            owner_department=registration.owner_department,
        )
        report = self.engine.validate(context)
        report.document_id = str(document.id)
        report.registration_id = str(registration.id)

        metadata = dict(registration.metadata_ or {})
        metadata["last_validation"] = report.to_dict()
        registration.metadata_ = metadata
        registration.status = (
            EskdRegistrationStatus.VALIDATED if report.passed else EskdRegistrationStatus.REJECTED
        )

        doc_metadata = dict(document.metadata_ or {})
        eskd_meta = dict(doc_metadata.get(ESKD_METADATA_KEY) or {})
        eskd_meta["validation_status"] = registration.status.value
        eskd_meta["validation_score"] = report.score
        eskd_meta["validation_passed"] = report.passed
        doc_metadata[ESKD_METADATA_KEY] = eskd_meta
        document.metadata_ = doc_metadata

        await self.db.flush()
        return report

    async def _load_registration(self, registration_id: uuid.UUID) -> EskdDocumentRegistration:
        registration = await self.db.get(EskdDocumentRegistration, registration_id)
        if registration is None:
            raise EskdValidationServiceError("Регистрация ЕСКД не найдена")
        return registration

    async def _load_document_text(self, document: Document) -> str:
        version = await self._resolve_document_version(document.id)
        if version is not None:
            text = self._load_extracted_text(document, version)
            if text.strip():
                return text
            chunks_text = await self._assemble_text_from_chunks(version.id)
            if chunks_text.strip():
                return chunks_text
        return ""

    async def _resolve_document_version(self, document_id: uuid.UUID) -> DocumentVersion | None:
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
        except (ObjectStorageError, json.JSONDecodeError, UnicodeDecodeError):
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
        parts = [chunk.text.strip() for chunk in result.scalars().all() if chunk.text and chunk.text.strip()]
        return "\n\n".join(parts)

    def _report_from_dict(
        self,
        payload: dict,
        *,
        registration: EskdDocumentRegistration,
    ) -> EskdValidationReport:
        from app.eskd.validation.schemas import EskdCheckResult

        checks = [
            EskdCheckResult(
                code=item["code"],
                title=item["title"],
                passed=item["passed"],
                severity=item["severity"],
                message=item["message"],
                gost_reference=item.get("gost_reference"),
                details=item.get("details") or {},
            )
            for item in payload.get("checks") or []
            if isinstance(item, dict)
        ]
        return EskdValidationReport(
            passed=bool(payload.get("passed")),
            score=float(payload.get("score") or 0),
            summary=str(payload.get("summary") or ""),
            checks=checks,
            document_id=str(registration.document_id),
            registration_id=str(registration.id),
            designation=registration.designation,
            document_kind=registration.document_kind.value,
            text_available=bool(payload.get("text_available")),
            validated_at=str(payload.get("validated_at") or ""),
        )


def validate_designation_characters(designation: str) -> bool:
    normalized = normalize_designation(designation)
    return bool(normalized and ESKD_DESIGNATION_CHARS_RE.match(normalized))
