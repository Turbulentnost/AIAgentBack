from __future__ import annotations
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.documents.processor import chunk_text, extract_text
from app.documents.storage import object_storage
from app.models.document import Document
from app.schemas.document import DocumentCreate
class DocumentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
    async def upload(self, data: DocumentCreate, content: bytes, mime_type: str) -> Document:
        storage_key = str(uuid.uuid4())
        object_storage.put_object(storage_key, content, mime_type)
        document = Document(**data.model_dump(), storage_key=storage_key, mime_type=mime_type)
        self.db.add(document)
        await self.db.flush()
        _ = chunk_text(extract_text(content, mime_type))
        return document
