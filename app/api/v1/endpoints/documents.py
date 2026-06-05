from __future__ import annotations
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from app.api.deps import CurrentUser, DbSession
from app.documents.storage import ObjectStorageError, object_storage
from app.knowledge_base.search import search_knowledge_base as search_knowledge_base_service
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.enums import DocumentType
from app.schemas.document import (
    ChunkSearchHit,
    ChunkSearchQuery,
    DocumentChunkRead,
    DocumentCreate,
    DocumentRead,
    DocumentVersionRead,
)
from app.services.document_service import DocumentMetadataSaveError, DocumentService
from app.services.permission_service import PermissionService
from app.workers.tasks import process_document
from sqlalchemy import select
router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    db: DbSession,
    current_user: CurrentUser,
    file: Annotated[UploadFile, File(...)],
    title: Annotated[str | None, Form()] = None,
    document_type: Annotated[DocumentType, Form()] = DocumentType.OTHER,
    department_id: Annotated[uuid.UUID | None, Form()] = None,
    task_id: Annotated[uuid.UUID | None, Form()] = None,
    is_knowledge_base: Annotated[bool, Form()] = False,
    source_url: Annotated[str | None, Form()] = None,
    metadata: Annotated[str | None, Form()] = None,
):
    content = await file.read()
    document = None
    try:
        parsed_metadata = _parse_metadata(metadata)
        document = await DocumentService(db).upload(
            DocumentCreate(
                title=title or file.filename or "Без названия",
                original_filename=file.filename,
                document_type=document_type,
                department_id=department_id or current_user.department_id,
                task_id=task_id,
                is_knowledge_base=False,
                source_url=source_url,
                metadata=parsed_metadata,
            ),
            content,
            file.content_type or "application/octet-stream",
            original_filename=file.filename,
            uploaded_by_user_id=current_user.id,
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ObjectStorageError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except DocumentMetadataSaveError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        if document is not None and document.object_name:
            try:
                object_storage.delete_object(document.object_name)
            except ObjectStorageError as cleanup_exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Файл загружен в MinIO, но транзакция PostgreSQL не завершилась. "
                        "Автоматически удалить объект из MinIO не удалось; нужна очистка вручную."
                    ),
                ) from cleanup_exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Файл загружен в MinIO, но транзакция PostgreSQL не завершилась. Объект удалён из MinIO.",
        ) from exc
    return document


@router.get("", response_model=list[DocumentRead])
async def list_documents(db: DbSession, current_user: CurrentUser):
    stmt = select(Document).order_by(Document.created_at.desc())
    if not current_user.is_superuser and current_user.department_id is not None:
        stmt = stmt.where(Document.department_id == current_user.department_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document_legacy(
    db: DbSession,
    current_user: CurrentUser,
    file: Annotated[UploadFile, File(...)],
    title: Annotated[str | None, Form()] = None,
    document_type: Annotated[DocumentType, Form()] = DocumentType.OTHER,
    department_id: Annotated[uuid.UUID | None, Form()] = None,
    task_id: Annotated[uuid.UUID | None, Form()] = None,
    is_knowledge_base: Annotated[bool, Form()] = False,
    source_url: Annotated[str | None, Form()] = None,
    metadata: Annotated[str | None, Form()] = None,
):
    return await upload_document(
        db=db,
        current_user=current_user,
        file=file,
        title=title,
        document_type=document_type,
        department_id=department_id,
        task_id=task_id,
        is_knowledge_base=is_knowledge_base,
        source_url=source_url,
        metadata=metadata,
    )


@router.post("/search", response_model=list[ChunkSearchHit])
async def search_knowledge_base(query: ChunkSearchQuery, db: DbSession, current_user: CurrentUser):
    return await search_knowledge_base_service(
        query=query.query,
        db=db,
        user=current_user,
        top_k=query.top_k,
        document_types=query.document_types,
        department_ids=query.department_ids,
        document_version_id=query.document_version_id,
        access_scopes=query.access_scopes,
        knowledge_base_id=query.knowledge_base_id,
        agent_id=query.agent_id,
    )


@router.post("/{document_id}/parse")
async def parse_document(document_id: uuid.UUID, current_user: CurrentUser):
    task = process_document.delay(str(document_id))
    return {"celery_task_id": task.id, "document_id": str(document_id), "status": "queued"}


@router.get("/{document_id}/versions", response_model=list[DocumentVersionRead])
async def list_document_versions(db: DbSession, current_user: CurrentUser, document_id: uuid.UUID):
    if not await PermissionService(db).can_access_document(current_user, document_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к документу")
    result = await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc(), DocumentVersion.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/versions/{document_version_id}/chunks", response_model=list[DocumentChunkRead])
async def list_document_version_chunks(db: DbSession, current_user: CurrentUser, document_version_id: uuid.UUID):
    version = await db.get(DocumentVersion, document_version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Версия документа не найдена")
    if not await PermissionService(db).can_access_document(current_user, version.document_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к документу")
    result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.document_version_id == document_version_id)
        .order_by(DocumentChunk.chunk_index.asc())
    )
    return list(result.scalars().all())


def _parse_metadata(raw_metadata: str | None) -> dict | None:
    if not raw_metadata:
        return None
    try:
        parsed = json.loads(raw_metadata)
    except json.JSONDecodeError as exc:
        raise ValueError("Поле metadata должно быть валидным JSON-объектом") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Поле metadata должно быть JSON-объектом")
    return parsed
