from __future__ import annotations
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from app.api.deps import CurrentUser, DbSession
from app.knowledge_base.retriever import retriever
from app.models.document import DocumentChunk, DocumentVersion
from app.models.enums import DocumentType
from app.schemas.document import (
    ChunkSearchHit,
    ChunkSearchQuery,
    DocumentChunkRead,
    DocumentCreate,
    DocumentRead,
    DocumentVersionRead,
)
from app.services.document_service import DocumentService
from sqlalchemy import select
router = APIRouter(prefix="/documents", tags=["documents"])

@router.post("", response_model=DocumentRead)
async def upload_document(
    db: DbSession,
    current_user: CurrentUser,
    file: Annotated[UploadFile, File(...)],
    title: Annotated[str, Form(...)],
    document_type: Annotated[DocumentType, Form()] = DocumentType.OTHER,
    department_id: Annotated[uuid.UUID | None, Form()] = None,
    task_id: Annotated[uuid.UUID | None, Form()] = None,
    is_knowledge_base: Annotated[bool, Form()] = False,
):
    content = await file.read()
    document = await DocumentService(db).upload(
        DocumentCreate(
            title=title,
            original_filename=file.filename,
            document_type=document_type,
            department_id=department_id,
            task_id=task_id,
            is_knowledge_base=is_knowledge_base,
        ),
        content,
        file.content_type or "application/octet-stream",
        original_filename=file.filename,
        uploaded_by_user_id=current_user.id,
    )
    return document

@router.post("/search", response_model=list[ChunkSearchHit])
async def search_knowledge_base(query: ChunkSearchQuery):
    hits = await retriever.retrieve(query.query, top_k=query.top_k)
    return [ChunkSearchHit(content=h.get("payload", {}).get("content", ""), score=h.get("score", 0.0), metadata=h.get("payload")) for h in hits]


@router.get("/{document_id}/versions", response_model=list[DocumentVersionRead])
async def list_document_versions(db: DbSession, document_id: uuid.UUID):
    result = await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc(), DocumentVersion.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/versions/{document_version_id}/chunks", response_model=list[DocumentChunkRead])
async def list_document_version_chunks(db: DbSession, document_version_id: uuid.UUID):
    result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.document_version_id == document_version_id)
        .order_by(DocumentChunk.chunk_index.asc())
    )
    return list(result.scalars().all())
