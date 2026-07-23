from __future__ import annotations

import hashlib
import mimetypes
import uuid
from pathlib import Path

from app.schemas.document import DocumentCreate
from app.schemas.document_card import DocumentCardFolderImportResult, DocumentCardImportItem
from app.services.document_folder_scan import DocumentFolderScanError, iter_supported_files, resolve_document_code


class DocumentFolderImportServiceError(ValueError):
    pass


class DocumentFolderImportService:
    def __init__(self, db) -> None:
        self.db = db

    async def import_folder(
        self,
        folder_path: str | Path,
        *,
        uploaded_by_user_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        recursive: bool = True,
        dry_run: bool = False,
        is_knowledge_base: bool = True,
    ) -> DocumentCardFolderImportResult:
        from app.models.enums import DocumentType
        from app.services.document_card_service import DocumentCardService, DocumentCardServiceError
        from app.services.document_service import DocumentService

        document_service = DocumentService(self.db)
        card_service = DocumentCardService(self.db)
        root = Path(folder_path)
        if not root.exists():
            raise DocumentFolderImportServiceError(f"Папка не найдена: {root}")
        if not root.is_dir():
            raise DocumentFolderImportServiceError(f"Указанный путь не является папкой: {root}")

        files = iter_supported_files(root, recursive=recursive)
        if not files:
            raise DocumentFolderImportServiceError(f"В папке нет поддерживаемых файлов: {root}")

        existing_checksums = await self._load_existing_checksums()
        existing_codes = await self._load_existing_codes()
        checksum_to_document_id = await self._load_checksum_to_document_id()
        create_cards = await self._cards_enabled()

        created = 0
        skipped = 0
        failed = 0
        items: list[DocumentCardImportItem] = []

        for file_path in files:
            relative_path = file_path.relative_to(root).as_posix()
            source_path = str(file_path)
            try:
                content = file_path.read_bytes()
                checksum = hashlib.sha256(content).hexdigest()
                if checksum in existing_checksums:
                    skipped += 1
                    document_id = checksum_to_document_id.get(checksum)
                    if document_id is not None and not dry_run:
                        await self._update_import_metadata(
                            document_id,
                            source_path=source_path,
                            folder_root=str(root),
                            relative_path=relative_path,
                        )
                    items.append(
                        DocumentCardImportItem(
                            source_path=source_path,
                            relative_path=relative_path,
                            status="skipped",
                            document_id=document_id,
                            message="Документ с таким checksum уже импортирован",
                        )
                    )
                    continue

                mime_type = self._guess_mime_type(file_path)
                title = file_path.stem.strip() or file_path.name
                document_code = resolve_document_code(
                    title=title,
                    filename=file_path.name,
                    existing_codes=existing_codes,
                    checksum=checksum,
                )
                metadata = {
                    "document_code": document_code,
                    "document_name": title,
                    "code": document_code,
                    "upload_source": "folder_import",
                    "import_source_path": source_path,
                    "import_folder_root": str(root),
                    "import_relative_path": relative_path,
                    "original_storage_location": source_path,
                }

                if dry_run:
                    created += 1
                    items.append(
                        DocumentCardImportItem(
                            source_path=source_path,
                            relative_path=relative_path,
                            status="created",
                            document_code=document_code,
                            document_name=title,
                            message="dry-run: будет создан",
                        )
                    )
                    existing_codes.add(document_code)
                    continue

                document = await document_service.upload(
                    DocumentCreate(
                        title=title,
                        original_filename=file_path.name,
                        document_type=DocumentType.REGULATION,
                        department_id=department_id,
                        is_knowledge_base=is_knowledge_base,
                        relative_path=relative_path,
                        metadata=metadata,
                    ),
                    content,
                    mime_type,
                    original_filename=file_path.name,
                    uploaded_by_user_id=uploaded_by_user_id,
                )
                card_id = None
                if create_cards:
                    card = await card_service.create_from_document(document)
                    card.document_code = document_code
                    card.document_name = title
                    card.original_storage_location = source_path
                    card.attachments = [file_path.name]
                    card_id = card.id
                await self.db.flush()

                existing_checksums.add(checksum)
                existing_codes.add(document_code)
                checksum_to_document_id[checksum] = document.id
                created += 1
                items.append(
                    DocumentCardImportItem(
                        source_path=source_path,
                        relative_path=relative_path,
                        status="created",
                        document_id=document.id,
                        card_id=card_id,
                        document_code=document_code,
                        document_name=title,
                    )
                )
            except (DocumentCardServiceError, ValueError) as exc:
                failed += 1
                items.append(
                    DocumentCardImportItem(
                        source_path=source_path,
                        relative_path=relative_path,
                        status="failed",
                        message=str(exc),
                    )
                )
            except OSError as exc:
                failed += 1
                items.append(
                    DocumentCardImportItem(
                        source_path=source_path,
                        relative_path=relative_path,
                        status="failed",
                        message=f"Ошибка чтения файла: {exc}",
                    )
                )

        return DocumentCardFolderImportResult(
            folder_path=str(root),
            total_files=len(files),
            created=created,
            skipped=skipped,
            failed=failed,
            dry_run=dry_run,
            items=items,
        )

    def scan_folder(self, folder_path: str | Path, *, recursive: bool = True) -> list[dict]:
        from app.services.document_folder_scan import scan_folder as scan_folder_files

        try:
            return scan_folder_files(folder_path, recursive=recursive)
        except DocumentFolderScanError as exc:
            raise DocumentFolderImportServiceError(str(exc)) from exc

    def _guess_mime_type(self, file_path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(file_path.name)
        if mime_type:
            return mime_type
        extension = file_path.suffix.lower()
        fallback = {
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".doc": "application/msword",
            ".xls": "application/vnd.ms-excel",
        }
        return fallback.get(extension, "application/octet-stream")

    async def _load_existing_checksums(self) -> set[str]:
        return set((await self._load_checksum_to_document_id()).keys())

    async def _load_checksum_to_document_id(self) -> dict[str, uuid.UUID]:
        from sqlalchemy import select

        from app.models.document import Document

        result = await self.db.execute(
            select(Document.checksum, Document.id).where(Document.checksum.is_not(None))
        )
        return {checksum: document_id for checksum, document_id in result.all() if checksum}

    async def _update_import_metadata(
        self,
        document_id: uuid.UUID,
        *,
        source_path: str,
        folder_root: str,
        relative_path: str,
    ) -> None:
        from app.models.document import Document

        document = await self.db.get(Document, document_id)
        if document is None:
            return
        metadata = dict(document.metadata_ or {})
        metadata.update(
            {
                "upload_source": "folder_import",
                "import_source_path": source_path,
                "import_folder_root": folder_root,
                "import_relative_path": relative_path,
                "original_storage_location": source_path,
            }
        )
        document.metadata_ = metadata
        document.is_knowledge_base = True
        await self.db.flush()

    async def _load_existing_codes(self) -> set[str]:
        from sqlalchemy import select
        from sqlalchemy.exc import ProgrammingError

        from app.models.document import Document

        codes: set[str] = set()
        try:
            from app.models.document_card import QmsDocumentCard

            result = await self.db.execute(select(QmsDocumentCard.document_code))
            codes.update(value for value in result.scalars().all() if value)
        except ProgrammingError:
            await self.db.rollback()
            result = await self.db.execute(select(Document.metadata_).where(Document.metadata_.is_not(None)))
            for metadata in result.scalars().all():
                if not isinstance(metadata, dict):
                    continue
                for key in ("document_code", "code"):
                    value = metadata.get(key)
                    if value:
                        codes.add(str(value))
        return codes

    async def _cards_enabled(self) -> bool:
        from sqlalchemy import text
        from sqlalchemy.exc import ProgrammingError

        try:
            await self.db.execute(text("SELECT 1 FROM document_cards LIMIT 1"))
            return True
        except ProgrammingError:
            await self.db.rollback()
            return False
