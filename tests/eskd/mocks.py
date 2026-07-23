from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.eskd.validation.rules import EskdValidationContext
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.document_card import QmsDocumentCard
from app.models.enums import (
    DocumentCardStatus,
    DocumentProcessingStatus,
    DocumentType,
    EskdDocumentKind,
    EskdRegistrationStatus,
    QmsDocumentKind,
    QmsLevel,
    TextExtractStatus,
)
from app.models.eskd_registration import EskdDocumentRegistration

DEFAULT_DESIGNATION = "ABVG.123456.001"
DEFAULT_TITLE = "Корпус"
DEFAULT_OWNER_DEPARTMENT = "ОКР"


def drawing_inscription_text(
    *,
    designation: str = DEFAULT_DESIGNATION,
    title: str = DEFAULT_TITLE,
    scale: str = "1:2",
    sheet: int = 1,
    sheets: int = 2,
) -> str:
    return (
        f"Обозначение {designation}\n"
        f"Наименование {title}\n"
        f"Масштаб {scale}\n"
        f"Лист {sheet}\n"
        f"Листов {sheets}\n"
    )


def specification_table_text(
    *,
    designation: str = DEFAULT_DESIGNATION,
    title: str = "Спецификация корпуса",
) -> str:
    return (
        f"Обозначение {designation}\n"
        f"Наименование {title}\n"
        "Поз. Обозначение Наименование Кол. Примеч.\n"
        "1 ABVG.111111.001 Болт 4\n"
        "2 ABVG.222222.001 Гайка 4\n"
    )


def assembly_drawing_text(
    *,
    designation: str = "ABVG.123456.001СБ",
    title: str = "Сборочный чертёж корпуса",
) -> str:
    return drawing_inscription_text(designation=designation, title=title)


def minio_extracted_text_payload(
    text: str,
    *,
    pages: int | None = None,
    source: str = "pdf_parser",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text": text,
        "pages": pages if pages is not None else max(1, text.count("\n") // 5 + 1),
        "source": source,
    }
    if extra:
        payload.update(extra)
    return payload


def minio_extracted_text_bytes(
    text: str,
    *,
    pages: int | None = None,
    source: str = "pdf_parser",
    extra: dict[str, Any] | None = None,
) -> bytes:
    return json.dumps(minio_extracted_text_payload(text, pages=pages, source=source, extra=extra)).encode("utf-8")


def make_document(**overrides: Any) -> Document:
    doc_id = overrides.pop("id", uuid.uuid4())
    designation = overrides.pop("designation", DEFAULT_DESIGNATION)
    defaults: dict[str, Any] = {
        "id": doc_id,
        "title": DEFAULT_TITLE,
        "original_filename": f"{designation}.pdf",
        "content_type": "application/pdf",
        "document_type": DocumentType.KD,
        "text_extract_status": TextExtractStatus.EXTRACTED,
        "processing_status": DocumentProcessingStatus.TEXT_EXTRACTED,
        "extracted_text_object_name": f"extracted/{doc_id}.json",
        "metadata_": {},
    }
    defaults.update(overrides)
    return Document(**defaults)


def make_document_version(document: Document, **overrides: Any) -> DocumentVersion:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "document_id": document.id,
        "version_number": document.version or 1,
        "version_label": "v1",
        "original_filename": document.original_filename,
        "content_type": document.content_type,
        "is_current": True,
        "text_extract_status": document.text_extract_status,
        "extracted_text_object_name": document.extracted_text_object_name,
        "processing_status": document.processing_status,
    }
    defaults.update(overrides)
    return DocumentVersion(**defaults)


def make_qms_document_card(document: Document, **overrides: Any) -> QmsDocumentCard:
    designation = overrides.pop("document_code", DEFAULT_DESIGNATION)
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "document_id": document.id,
        "document_code": designation,
        "document_name": document.title,
        "document_type": QmsDocumentKind.STO,
        "qms_level": QmsLevel.TECHNICAL,
        "status": DocumentCardStatus.DRAFT,
        "owner_department": DEFAULT_OWNER_DEPARTMENT,
    }
    defaults.update(overrides)
    return QmsDocumentCard(**defaults)


def make_eskd_registration(document: Document, **overrides: Any) -> EskdDocumentRegistration:
    designation = overrides.pop("designation", DEFAULT_DESIGNATION)
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "document_id": document.id,
        "designation": designation,
        "document_kind": EskdDocumentKind.DRAWING,
        "status": EskdRegistrationStatus.REGISTERED,
        "owner_department": DEFAULT_OWNER_DEPARTMENT,
        "agent_slug": "nd_control_agent",
        "metadata_": {},
    }
    defaults.update(overrides)
    return EskdDocumentRegistration(**defaults)


def make_document_chunk(
    version: DocumentVersion,
    *,
    chunk_index: int = 0,
    text: str,
) -> DocumentChunk:
    return DocumentChunk(
        id=uuid.uuid4(),
        document_id=version.document_id,
        document_version_id=version.id,
        chunk_index=chunk_index,
        text=text,
        content=text,
    )


def build_validation_context(**overrides: Any) -> EskdValidationContext:
    designation = overrides.pop("designation", DEFAULT_DESIGNATION)
    title = overrides.pop("document_title", DEFAULT_TITLE)
    document_text = overrides.pop(
        "document_text",
        drawing_inscription_text(designation=designation, title=title),
    )
    defaults: dict[str, Any] = {
        "designation": designation,
        "document_kind": EskdDocumentKind.DRAWING,
        "document_title": title,
        "original_filename": f"{designation}.pdf",
        "document_type": DocumentType.KD,
        "text_extract_status": TextExtractStatus.EXTRACTED,
        "document_text": document_text,
        "qms_document_code": designation,
        "owner_department": DEFAULT_OWNER_DEPARTMENT,
    }
    defaults.update(overrides)
    return EskdValidationContext(**defaults)


def build_valid_drawing_context(**overrides: Any) -> EskdValidationContext:
    return build_validation_context(**overrides)


def build_invalid_designation_context(**overrides: Any) -> EskdValidationContext:
    return build_validation_context(
        designation="INVALID CODE",
        document_title="x",
        original_filename=None,
        text_extract_status=None,
        document_text="",
        qms_document_code=None,
        owner_department=None,
        **overrides,
    )


def build_missing_designation_context(**overrides: Any) -> EskdValidationContext:
    return build_validation_context(
        designation=None,
        original_filename="drawing.pdf",
        text_extract_status=TextExtractStatus.NOT_STARTED,
        document_text="",
        qms_document_code=None,
        owner_department=None,
        **overrides,
    )


def build_specification_context(**overrides: Any) -> EskdValidationContext:
    designation = overrides.pop("designation", DEFAULT_DESIGNATION)
    title = overrides.pop("document_title", "Спецификация корпуса")
    return build_validation_context(
        designation=designation,
        document_kind=EskdDocumentKind.SPECIFICATION,
        document_title=title,
        document_text=specification_table_text(designation=designation, title=title),
        **overrides,
    )


def build_assembly_drawing_context(**overrides: Any) -> EskdValidationContext:
    designation = overrides.pop("designation", "ABVG.123456.001СБ")
    title = overrides.pop("document_title", "Сборочный чертёж корпуса")
    return build_validation_context(
        designation=designation,
        document_kind=EskdDocumentKind.ASSEMBLY_DRAWING,
        document_title=title,
        document_text=assembly_drawing_text(designation=designation, title=title),
        **overrides,
    )


def build_context_from_entities(
    registration: EskdDocumentRegistration,
    document: Document,
    *,
    card: QmsDocumentCard | None = None,
    document_text: str | None = None,
) -> EskdValidationContext:
    if document_text is None and registration.designation and document.title:
        document_text = drawing_inscription_text(
            designation=registration.designation,
            title=document.title,
        )
    return EskdValidationContext(
        designation=registration.designation,
        document_kind=registration.document_kind,
        document_title=document.title,
        original_filename=document.original_filename,
        document_type=document.document_type,
        text_extract_status=document.text_extract_status,
        document_text=document_text or "",
        qms_document_code=card.document_code if card else None,
        owner_department=registration.owner_department,
    )


def make_eskd_bundle(
    *,
    designation: str = DEFAULT_DESIGNATION,
    title: str = DEFAULT_TITLE,
    document_kind: EskdDocumentKind = EskdDocumentKind.DRAWING,
    with_card: bool = True,
    document_text: str | None = None,
) -> tuple[EskdDocumentRegistration, Document, DocumentVersion, QmsDocumentCard | None]:
    document = make_document(designation=designation, title=title)
    version = make_document_version(document)
    card = make_qms_document_card(document, document_code=designation) if with_card else None
    registration = make_eskd_registration(
        document,
        designation=designation,
        document_kind=document_kind,
        qms_document_card_id=card.id if card else None,
    )
    if document_text is not None:
        document.extracted_text_object_name = f"extracted/{document.id}.json"
        version.extracted_text_object_name = document.extracted_text_object_name
    return registration, document, version, card


def mock_validation_service_db(
    *,
    registration: EskdDocumentRegistration,
    document: Document,
    version: DocumentVersion | None = None,
    card: QmsDocumentCard | None = None,
    chunks: list[DocumentChunk] | None = None,
) -> AsyncMock:
    db = AsyncMock()

    async def _get(model, obj_id):  # type: ignore[no-untyped-def]
        if model is EskdDocumentRegistration and obj_id == registration.id:
            return registration
        if model is Document and obj_id == registration.document_id:
            return document
        if model is QmsDocumentCard and card is not None and obj_id == card.id:
            return card
        return None

    db.get = AsyncMock(side_effect=_get)

    version_rows = [version] if version is not None else []
    chunk_rows = chunks or []

    execute_results: list[Any] = []

    if version_rows:
        version_result = MagicMock()
        version_result.scalar_one_or_none = MagicMock(return_value=version_rows[0])
        execute_results.append(version_result)

    chunk_result = MagicMock()
    chunk_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=chunk_rows)))
    execute_results.append(chunk_result)

    db.execute = AsyncMock(side_effect=execute_results)
    db.flush = AsyncMock()
    return db


def mock_registration_service_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.scalar = AsyncMock(return_value=0)
    db.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
    )
    return db
