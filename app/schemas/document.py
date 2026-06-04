from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from app.models.enums import DocumentProcessingStatus, DocumentType, TextExtractStatus
from app.schemas.common import ORMModel
class DocumentCreate(BaseModel):
    title: str = Field(..., max_length=512)
    original_filename: str | None = None
    document_type: DocumentType = DocumentType.OTHER
    department_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    is_knowledge_base: bool = False
    source_url: str | None = None
    metadata: dict | None = None

    # Legacy aliases accepted by older clients.
    doc_type: DocumentType | None = None
    doc_metadata: dict | None = None

class DocumentRead(ORMModel):
    id: uuid.UUID
    title: str
    original_filename: str | None
    content_type: str | None
    file_size: int | None
    bucket_name: str | None
    object_name: str | None
    uploaded_by_user_id: uuid.UUID | None
    department_id: uuid.UUID | None
    task_id: uuid.UUID | None
    document_type: DocumentType
    processing_status: DocumentProcessingStatus
    is_knowledge_base: bool
    is_indexed: bool
    text_extract_status: TextExtractStatus
    extracted_text_object_name: str | None
    pages_count: int | None
    checksum: str | None
    version: int
    source_url: str | None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")

    # Legacy fields kept in API responses during transition.
    doc_type: DocumentType
    storage_key: str | None
    mime_type: str | None
    doc_metadata: dict | None
    created_at: datetime
    updated_at: datetime


class DocumentVersionRead(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    version_number: int
    version_label: str
    original_filename: str | None
    content_type: str | None
    file_size: int | None
    bucket_name: str | None
    object_name: str | None
    uploaded_by_user_id: uuid.UUID | None
    uploaded_at: datetime
    processing_status: DocumentProcessingStatus
    text_extract_status: TextExtractStatus
    extracted_text_object_name: str | None
    is_indexed: bool
    qdrant_collection: str | None
    qdrant_points_count: int | None
    checksum: str | None
    pages_count: int | None
    source_url: str | None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")

    # Legacy fields kept during transition.
    storage_key: str | None
    is_current: bool
    created_at: datetime
    updated_at: datetime

class ChunkSearchQuery(BaseModel):
    query: str
    top_k: int = 5
class ChunkSearchHit(BaseModel):
    content: str
    score: float
    document_id: uuid.UUID | None = None
    metadata: dict | None = None
