from __future__ import annotations
import hashlib
import uuid
from pathlib import PurePath
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.documents.processor import chunk_text, extract_text
from app.documents.storage import object_storage
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.enums import DocumentProcessingStatus, DocumentType, TextExtractStatus
from app.schemas.document import DocumentCreate


class DocumentMetadataSaveError(RuntimeError):
    pass


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
        self.validate_upload(content, mime_type)
        document_id = uuid.uuid4()
        document_type = data.doc_type or data.document_type
        original_name = original_filename or data.original_filename or f"{document_id}"
        safe_filename = self._safe_filename(original_name)
        object_name = self._build_object_name(
            document_id=document_id,
            document_type=document_type,
            task_id=data.task_id,
            is_knowledge_base=data.is_knowledge_base,
            filename=safe_filename,
        )
        checksum = hashlib.sha256(content).hexdigest()
        metadata = self._build_metadata(
            data=data,
            document_id=document_id,
            document_type=document_type,
            original_filename=original_name,
            object_name=object_name,
            mime_type=mime_type,
            file_size=len(content),
            checksum=checksum,
        )
        object_storage.put_object(object_name, content, mime_type)

        chunks: list[str] = []
        text_extract_status = TextExtractStatus.NOT_STARTED
        try:
            chunks = chunk_text(extract_text(content, mime_type))
            text_extract_status = TextExtractStatus.EXTRACTED
        except Exception:
            text_extract_status = TextExtractStatus.FAILED

        try:
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
                checksum=checksum,
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
        except Exception as exc:
            await self.db.rollback()
            try:
                object_storage.delete_object(object_name)
            except Exception as cleanup_exc:
                raise DocumentMetadataSaveError(
                    "Файл загружен в MinIO, но метаданные не сохранились в PostgreSQL. "
                    "Автоматически удалить объект из MinIO не удалось; нужна очистка вручную."
                ) from cleanup_exc
            raise DocumentMetadataSaveError(
                "Файл загружен в MinIO, но метаданные не сохранились в PostgreSQL. "
                "Загруженный объект удалён из MinIO."
            ) from exc

    def validate_upload(self, content: bytes, mime_type: str) -> None:
        if not content:
            raise ValueError("Файл пустой")
        if len(content) > settings.DOCUMENT_MAX_UPLOAD_SIZE_BYTES:
            raise ValueError(
                f"Файл превышает максимальный размер {settings.DOCUMENT_MAX_UPLOAD_SIZE_BYTES} байт"
            )
        if mime_type not in settings.document_allowed_content_types:
            raise ValueError(f"Тип файла не разрешён: {mime_type}")

    def _safe_filename(self, filename: str) -> str:
        name = PurePath(filename).name.strip().replace("\\", "_").replace("/", "_")
        return name or "document"

    def _build_object_name(
        self,
        *,
        document_id: uuid.UUID,
        document_type: DocumentType,
        task_id: uuid.UUID | None,
        is_knowledge_base: bool,
        filename: str,
    ) -> str:
        unique_filename = f"{document_id}_{filename}"
        if task_id and document_type == DocumentType.TASK_INPUT:
            return f"tasks/{task_id}/input/{unique_filename}"
        if task_id:
            return f"tasks/{task_id}/documents/{unique_filename}"
        if is_knowledge_base:
            return f"knowledge_base/{document_type.value}/{unique_filename}"
        return f"documents/{document_type.value}/{unique_filename}"

    def _build_metadata(
        self,
        *,
        data: DocumentCreate,
        document_id: uuid.UUID,
        document_type: DocumentType,
        original_filename: str,
        object_name: str,
        mime_type: str,
        file_size: int,
        checksum: str,
    ) -> dict:
        provided_metadata = data.doc_metadata or data.metadata or {}
        original_extension = PurePath(original_filename).suffix.lower()
        requires_ocr = bool(provided_metadata.get("requires_ocr", mime_type in {"image/png", "image/jpeg", "image/webp"}))
        return {
            **provided_metadata,
            "upload_source": provided_metadata.get("upload_source", "api"),
            "document_id": str(document_id),
            "document_type": document_type.value,
            "original_extension": original_extension,
            "requires_ocr": requires_ocr,
            "bucket_name": object_storage.bucket,
            "object_name": object_name,
            "content_type": mime_type,
            "file_size": file_size,
            "checksum": checksum,
            "version": 1,
            "task_id": str(data.task_id) if data.task_id else None,
            "department_id": str(data.department_id) if data.department_id else None,
            "is_knowledge_base": data.is_knowledge_base,
            "source_url": data.source_url,
        }
