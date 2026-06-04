from __future__ import annotations
from fastapi import APIRouter, File, Form, UploadFile
from app.api.deps import DbSession
from app.knowledge_base.retriever import retriever
from app.models.enums import DocumentType
from app.schemas.document import ChunkSearchHit, ChunkSearchQuery, DocumentCreate, DocumentRead
from app.services.document_service import DocumentService
router = APIRouter(prefix="/documents", tags=["documents"])
@router.post("", response_model=DocumentRead)
async def upload_document(db: DbSession, file: UploadFile = File(...), title: str = Form(...), doc_type: DocumentType = Form(DocumentType.OTHER)):
    content = await file.read()
    document = await DocumentService(db).upload(DocumentCreate(title=title, doc_type=doc_type), content, file.content_type or "application/octet-stream")
    return document
@router.post("/search", response_model=list[ChunkSearchHit])
async def search_knowledge_base(query: ChunkSearchQuery):
    hits = await retriever.retrieve(query.query, top_k=query.top_k)
    return [ChunkSearchHit(content=h.get("payload", {}).get("content", ""), score=h.get("score", 0.0), metadata=h.get("payload")) for h in hits]
