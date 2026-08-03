from __future__ import annotations

from app.services.document_folder_import_service import DocumentFolderImportService


class NdBulkImportService:
    """Массовый импорт базы НД из сетевых каталогов (ТЗ п. 11.2)."""

    async def import_folder(self, db, payload: NdBulkImportRequest) -> NdBulkImportResult:
        service = DocumentFolderImportService(db)
        try:
            result = await service.import_folder(
                payload.root_path,
                dry_run=payload.dry_run,
                recursive=True,
            )
        except Exception as exc:  # noqa: BLE001
            return NdBulkImportResult(
                scanned_files=0,
                imported_cards=0,
                skipped_files=0,
                errors=[str(exc)],
                dry_run=payload.dry_run,
            )
        created = sum(1 for item in result.items if item.status == "created")
        skipped = sum(1 for item in result.items if item.status == "skipped")
        failed = sum(1 for item in result.items if item.status == "failed")
        return NdBulkImportResult(
            scanned_files=len(result.items),
            imported_cards=created,
            skipped_files=skipped + failed,
            errors=[item.message for item in result.items if item.status == "failed" and item.message][:50],
            dry_run=payload.dry_run,
        )
