from __future__ import annotations
import json
import uuid
from io import BytesIO
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, DbSession
from app.documents.storage import ObjectStorageError, object_storage
from app.knowledge_base.search import search_knowledge_base as search_knowledge_base_service
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.enums import DocumentType
from app.schemas.common import Page
from app.schemas.document import (
    ChunkSearchHit,
    ChunkSearchQuery,
    DocumentChunkRead,
    DocumentCreate,
    DocumentListItem,
    DocumentRead,
    DocumentVersionRead,
)
from app.services.document_service import DocumentMetadataSaveError, DocumentService
from app.services.permission_service import PermissionService
from app.workers.tasks import process_document

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
    relative_path: Annotated[str | None, Form()] = None,
    metadata: Annotated[str | None, Form()] = None,
):
    content = await file.read()
    document = None
    try:
        parsed_metadata = _parse_metadata(metadata)
        resolved_relative_path = relative_path or file.filename
        document = await DocumentService(db).upload(
            DocumentCreate(
                title=title or file.filename or "Без названия",
                original_filename=file.filename,
                document_type=document_type,
                department_id=department_id or current_user.department_id,
                task_id=task_id,
                is_knowledge_base=False,
                source_url=source_url,
                relative_path=resolved_relative_path,
                metadata=parsed_metadata,
            ),
            content,
            file.content_type or "application/octet-stream",
            original_filename=file.filename,
            uploaded_by_user_id=current_user.id,
        )
        await db.commit()
        await db.refresh(document)
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


@router.get("", response_model=Page[DocumentListItem])
async def list_documents(
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=5000),
    query: str | None = None,
):
    filters = []
    normalized_query = (query or "").strip()
    if normalized_query:
        pattern = f"%{normalized_query}%"
        filters.append(
            or_(
                Document.title.ilike(pattern),
                Document.original_filename.ilike(pattern),
            )
        )

    count_stmt = select(func.count(Document.id))
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = int(await db.scalar(count_stmt) or 0)

    stmt = select(Document).order_by(Document.created_at.desc())
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.offset((page - 1) * size).limit(size)
    result = await db.execute(stmt)
    documents = list(result.scalars().all())

    permissions = PermissionService(db)
    items: list[DocumentListItem] = []
    for document in documents:
        base = DocumentRead.model_validate(document)
        items.append(
            DocumentListItem(
                **base.model_dump(),
                can_access=permissions.can_access_document_record(current_user, document),
            )
        )
    return Page(items=items, total=total, page=page, size=size)


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
async def parse_document(document_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    if not await PermissionService(db).can_access_document(current_user, document_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к документу")
    task = process_document.delay(str(document_id))
    return {"celery_task_id": task.id, "document_id": str(document_id), "status": "queued"}


@router.get("/{document_id}/file")
async def get_document_file(
    document_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    disposition: Literal["inline", "attachment"] = Query("attachment"),
):
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")
    if not await PermissionService(db).can_access_document(current_user, document_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к документу")
    if not document.object_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл документа не найден")

    try:
        content = object_storage.get_object(document.object_name)
    except ObjectStorageError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    filename = document.original_filename or f"{document.title}.bin"
    media_type = document.content_type or document.mime_type or "application/octet-stream"
    encoded_filename = quote(filename)
    headers = {
        "Content-Disposition": f'{disposition}; filename="{filename}"; filename*=UTF-8\'\'{encoded_filename}',
        "Content-Length": str(len(content)),
    }
    return StreamingResponse(BytesIO(content), media_type=media_type, headers=headers)


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


@router.get("/versions/{document_version_id}/extracted-text")
async def get_document_version_extracted_text(
    db: DbSession,
    current_user: CurrentUser,
    document_version_id: uuid.UUID,
):
    version = await db.get(DocumentVersion, document_version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Версия документа не найдена")
    if not await PermissionService(db).can_access_document(current_user, version.document_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к документу")
    object_name = version.extracted_text_object_name
    if not object_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Извлечённый текст для версии документа не найден")
    try:
        payload = json.loads(object_storage.get_object(object_name).decode("utf-8"))
    except ObjectStorageError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Некорректный JSON извлечённого текста") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Некорректный формат извлечённого текста")
    return payload


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
