from __future__ import annotations
import hashlib
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.documents.processor import chunk_text, extract_text
from app.documents.storage import object_storage
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.enums import DocumentProcessingStatus, TextExtractStatus
from app.schemas.document import DocumentCreate

class DocumentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upload(
        self,
        data: DocumentCreate,
        content: bytes,
        mime_type: str,
        *,
        original_filename: str | None = None,
        uploaded_by_user_id: uuid.UUID | None = None,
    ) -> Document:
        document_id = uuid.uuid4()
        document_type = data.doc_type or data.document_type
        metadata = data.doc_metadata or data.metadata
        safe_filename = original_filename or data.original_filename or f"{document_id}"
        object_name = f"documents/{document_id}/{safe_filename}"
        object_storage.put_object(object_name, content, mime_type)

        chunks: list[str] = []
        text_extract_status = TextExtractStatus.NOT_STARTED
        try:
            chunks = chunk_text(extract_text(content, mime_type))
            text_extract_status = TextExtractStatus.EXTRACTED
        except Exception:
            text_extract_status = TextExtractStatus.FAILED

        document = Document(
            id=document_id,
            title=data.title,
            original_filename=original_filename or data.original_filename,
            content_type=mime_type,
            file_size=len(content),
            bucket_name=object_storage.bucket,
            object_name=object_name,
            uploaded_by_user_id=uploaded_by_user_id,
            department_id=data.department_id,
            task_id=data.task_id,
            document_type=document_type,
            processing_status=DocumentProcessingStatus.UPLOADED,
            is_knowledge_base=data.is_knowledge_base,
            is_indexed=False,
            text_extract_status=text_extract_status,
            checksum=hashlib.sha256(content).hexdigest(),
            version=1,
            source_url=data.source_url,
            metadata_=metadata,
            # Legacy fields.
            doc_type=document_type,
            storage_key=object_name,
            mime_type=mime_type,
            doc_metadata=metadata,
        )
        self.db.add(document)
        document_version = DocumentVersion(
            document_id=document_id,
            version_number=1,
            version_label="v1",
            original_filename=original_filename or data.original_filename,
            content_type=mime_type,
            file_size=len(content),
            bucket_name=object_storage.bucket,
            object_name=object_name,
            uploaded_by_user_id=uploaded_by_user_id,
            processing_status=DocumentProcessingStatus.UPLOADED,
            text_extract_status=text_extract_status,
            is_indexed=False,
            checksum=document.checksum,
            source_url=data.source_url,
            metadata_=metadata,
            storage_key=object_name,
            is_current=True,
        )
        self.db.add(document_version)
        await self.db.flush()

        for index, chunk in enumerate(chunks):
            self.db.add(
                DocumentChunk(
                    document_id=document_id,
                    document_version_id=document_version.id,
                    chunk_index=index,
                    text=chunk,
                    token_count=len(chunk.split()),
                    qdrant_collection=settings.QDRANT_COLLECTION,
                    embedding_model=settings.LLM_EMBEDDING_MODEL,
                    is_indexed=False,
                    metadata_={"source": "upload", "chunk_size": len(chunk)},
                    content=chunk,
                    chunk_metadata={"source": "upload", "chunk_size": len(chunk)},
                )
            )
        await self.db.flush()
        return document
