from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from app.models.enums import DocumentType
from app.schemas.common import ORMModel
class DocumentCreate(BaseModel):
    title: str = Field(..., max_length=512)
    doc_type: DocumentType = DocumentType.OTHER
    doc_metadata: dict | None = None
    department_id: uuid.UUID | None = None
class DocumentRead(ORMModel):
    id: uuid.UUID
    title: str
    doc_type: DocumentType
    storage_key: str | None
    mime_type: str | None
    created_at: datetime
class ChunkSearchQuery(BaseModel):
    query: str
    top_k: int = 5
class ChunkSearchHit(BaseModel):
    content: str
    score: float
    document_id: uuid.UUID | None = None
    metadata: dict | None = None
