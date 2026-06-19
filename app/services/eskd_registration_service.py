from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.eskd.constants import (
    ESKD_METADATA_KEY,
    ESKD_REGISTRATION_SOURCE,
    ND_CONTROL_AGENT_SLUG,
)
from app.models.document import Document
from app.models.document_card import QmsDocumentCard
from app.models.enums import DocumentCardStatus, DocumentType, EskdDocumentKind, EskdRegistrationStatus
from app.models.eskd_registration import EskdDocumentRegistration
from app.models.nd_control_registry import NdControlDepartment
from app.models.user import User
from app.schemas.document import DocumentCreate, DocumentRead
from app.schemas.document_card import DocumentCardRead
from app.schemas.eskd import (
    EskdDocumentUploadRegisterRequest,
    EskdRegisterExistingRequest,
    EskdUploadRegisterResponse,
)
from app.services.document_card_service import DocumentCardService, DocumentCardServiceError
from app.services.document_card_utils import extract_document_code, fallback_document_code, infer_document_kind, infer_qms_level
from app.services.document_service import DocumentService

from app.eskd.designation import ESKD_DESIGNATION_CHARS_RE, normalize_designation


class EskdRegistrationServiceError(ValueError):
    pass


class EskdRegistrationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_registrations(
        self,
        *,
        page: int = 1,
        size: int = 50,
        status: EskdRegistrationStatus | None = None,
        designation_query: str | None = None,
    ) -> tuple[list[EskdDocumentRegistration], int]:
        filters = []
        if status is not None:
            filters.append(EskdDocumentRegistration.status == status)
        if designation_query:
            needle = f"%{designation_query.strip()}%"
            filters.append(EskdDocumentRegistration.designation.ilike(needle))

        count_stmt = select(func.count()).select_from(EskdDocumentRegistration)
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = int(await self.db.scalar(count_stmt) or 0)

        stmt = select(EskdDocumentRegistration).order_by(EskdDocumentRegistration.created_at.desc())
        if filters:
            stmt = stmt.where(*filters)
        stmt = stmt.offset(max(page - 1, 0) * size).limit(size)
        result = await self.db.execute(stmt)
        return list(result.scalars().all()), total

    async def get_registration(self, registration_id: uuid.UUID) -> EskdDocumentRegistration:
        item = await self.db.get(EskdDocumentRegistration, registration_id)
        if item is None:
            raise EskdRegistrationServiceError("Регистрация ЕСКД не найдена")
        return item

    async def get_registration_by_document(self, document_id: uuid.UUID) -> EskdDocumentRegistration | None:
        result = await self.db.execute(
            select(EskdDocumentRegistration).where(EskdDocumentRegistration.document_id == document_id)
        )
        return result.scalar_one_or_none()

    async def upload_and_register(
        self,
        *,
        content: bytes,
        mime_type: str,
        original_filename: str | None,
        payload: EskdDocumentUploadRegisterRequest,
        current_user: User,
    ) -> EskdUploadRegisterResponse:
        if not content:
            raise EskdRegistrationServiceError("Файл пустой")

        designation = self._normalize_designation(payload.designation, original_filename)
        await self._validate_nd_control_department(payload.nd_control_department_id)

        title = (payload.title or original_filename or designation or "Документ ЕСКД").strip()
        metadata = self._build_eskd_metadata(
            designation=designation,
            document_kind=payload.document_kind,
            owner_department=payload.owner_department,
            nd_control_department_id=payload.nd_control_department_id,
            notes=payload.notes,
        )

        document = await DocumentService(self.db).upload(
            DocumentCreate(
                title=title[:512],
                original_filename=original_filename,
                document_type=DocumentType.KD,
                department_id=payload.department_id or current_user.department_id,
                is_knowledge_base=payload.is_knowledge_base,
                relative_path=payload.relative_path or original_filename,
                metadata=metadata,
            ),
            content,
            mime_type,
            original_filename=original_filename,
            uploaded_by_user_id=current_user.id,
        )

        card = await self._ensure_qms_card(
            document=document,
            designation=designation,
            document_kind=payload.document_kind,
            owner_department=payload.owner_department,
        )

        registration = EskdDocumentRegistration(
            document_id=document.id,
            qms_document_card_id=card.id if card else None,
            nd_control_department_id=payload.nd_control_department_id,
            registered_by_user_id=current_user.id,
            agent_slug=ND_CONTROL_AGENT_SLUG,
            designation=designation,
            document_kind=payload.document_kind,
            status=EskdRegistrationStatus.REGISTERED,
            owner_department=payload.owner_department,
            notes=payload.notes,
            metadata_=metadata.get(ESKD_METADATA_KEY),
        )
        self.db.add(registration)
        await self.db.flush()

        celery_task_id = None
        processing_queued = False
        if payload.start_processing:
            celery_task_id = self._enqueue_processing(document.id)
            registration.celery_task_id = celery_task_id
            registration.status = EskdRegistrationStatus.PROCESSING
            processing_queued = True
            await self.db.flush()

        return EskdUploadRegisterResponse(
            registration=registration,
            document=DocumentRead.model_validate(document),
            document_card=DocumentCardRead.model_validate(card) if card else None,
            processing_queued=processing_queued,
            celery_task_id=celery_task_id,
        )

    async def register_existing_document(
        self,
        document_id: uuid.UUID,
        *,
        payload: EskdRegisterExistingRequest,
        current_user: User,
    ) -> EskdUploadRegisterResponse:
        document = await self.db.get(Document, document_id)
        if document is None:
            raise EskdRegistrationServiceError("Документ не найден")

        existing = await self.get_registration_by_document(document_id)
        if existing is not None:
            raise EskdRegistrationServiceError("Документ уже зарегистрирован для проверки ЕСКД")

        await self._validate_nd_control_department(payload.nd_control_department_id)
        designation = self._normalize_designation(payload.designation, document.original_filename)
        metadata = dict(document.metadata_ or {})
        metadata[ESKD_METADATA_KEY] = {
            **(metadata.get(ESKD_METADATA_KEY) or {}),
            **self._build_eskd_metadata(
                designation=designation,
                document_kind=payload.document_kind,
                owner_department=payload.owner_department,
                nd_control_department_id=payload.nd_control_department_id,
                notes=payload.notes,
            )[ESKD_METADATA_KEY],
        }
        document.metadata_ = metadata
        if document.document_type != DocumentType.KD:
            document.document_type = DocumentType.KD
            document.doc_type = DocumentType.KD

        card = await self._ensure_qms_card(
            document=document,
            designation=designation,
            document_kind=payload.document_kind,
            owner_department=payload.owner_department,
        )

        registration = EskdDocumentRegistration(
            document_id=document.id,
            qms_document_card_id=card.id if card else None,
            nd_control_department_id=payload.nd_control_department_id,
            registered_by_user_id=current_user.id,
            agent_slug=ND_CONTROL_AGENT_SLUG,
            designation=designation,
            document_kind=payload.document_kind,
            status=EskdRegistrationStatus.REGISTERED,
            owner_department=payload.owner_department,
            notes=payload.notes,
            metadata_=metadata.get(ESKD_METADATA_KEY),
        )
        self.db.add(registration)
        await self.db.flush()

        celery_task_id = None
        processing_queued = False
        if payload.start_processing:
            celery_task_id = self._enqueue_processing(document.id)
            registration.celery_task_id = celery_task_id
            registration.status = EskdRegistrationStatus.PROCESSING
            processing_queued = True
            await self.db.flush()

        return EskdUploadRegisterResponse(
            registration=registration,
            document=DocumentRead.model_validate(document),
            document_card=DocumentCardRead.model_validate(card) if card else None,
            processing_queued=processing_queued,
            celery_task_id=celery_task_id,
        )

    async def _ensure_qms_card(
        self,
        *,
        document: Document,
        designation: str | None,
        document_kind: EskdDocumentKind,
        owner_department: str | None,
    ) -> QmsDocumentCard | None:
        card_service = DocumentCardService(self.db)
        try:
            card = await card_service.get_by_document_id(document.id)
            if card is None:
                card = await card_service.create_from_document(document)
        except DocumentCardServiceError:
            return None

        code = designation or extract_document_code(
            title=document.title,
            original_filename=document.original_filename,
            metadata=document.metadata_,
        ) or fallback_document_code(str(document.id))
        card.document_code = code.strip().upper()[:64]
        card.document_name = document.title
        card.document_type = infer_document_kind(card.document_code)
        card.qms_level = infer_qms_level(card.document_type)
        card.owner_department = owner_department
        card.status = DocumentCardStatus.DRAFT
        card.scope = f"Конструкторская документация ({document_kind.value}) — проверка ЕСКД"
        card.electronic_storage_location = card.electronic_storage_location or "DMS/Knowledge Base"
        card.original_storage_location = card.original_storage_location or document.object_name
        await self.db.flush()
        return card

    async def _validate_nd_control_department(self, department_id: uuid.UUID | None) -> None:
        if department_id is None:
            return
        dept = await self.db.get(NdControlDepartment, department_id)
        if dept is None:
            raise EskdRegistrationServiceError("Отдел nd_control не найден")

    def _normalize_designation(self, designation: str | None, filename: str | None) -> str | None:
        if designation and designation.strip():
            value = normalize_designation(designation)
            if not value or not ESKD_DESIGNATION_CHARS_RE.match(value):
                raise EskdRegistrationServiceError(
                    "Некорректное обозначение ЕСКД: допустимы буквы, цифры, точка, дефис, слэш"
                )
            return value
        if filename:
            stem = filename.rsplit(".", 1)[0].strip().upper()
            if stem and ESKD_DESIGNATION_CHARS_RE.match(stem):
                return stem
        return None

    def _build_eskd_metadata(
        self,
        *,
        designation: str | None,
        document_kind: EskdDocumentKind,
        owner_department: str | None,
        nd_control_department_id: uuid.UUID | None,
        notes: str | None,
    ) -> dict:
        return {
            ESKD_METADATA_KEY: {
                "module": "eskd",
                "agent_slug": ND_CONTROL_AGENT_SLUG,
                "registration_source": ESKD_REGISTRATION_SOURCE,
                "designation": designation,
                "document_kind": document_kind.value,
                "owner_department": owner_department,
                "nd_control_department_id": str(nd_control_department_id) if nd_control_department_id else None,
                "notes": notes,
                "validation_status": EskdRegistrationStatus.PENDING_VALIDATION.value,
            }
        }

    def _enqueue_processing(self, document_id: uuid.UUID) -> str:
        from app.workers.tasks import process_document

        async_result = process_document.apply_async(args=[str(document_id)], queue="agents")
        return async_result.id
