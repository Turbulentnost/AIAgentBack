from __future__ import annotations

import re
import uuid
from pathlib import PurePath

from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.storage import object_storage
from app.models.document import Document, DocumentVersion
from app.models.enums import NdChangeDraftFileStatus
from app.services.document_editing.change_applier import ChangeApplier
from app.services.document_editing.change_locator import ChangeLocator
from app.services.document_editing.diff_service import DiffService
from app.services.document_editing.docx_editor import DocxEditor
from app.services.document_editing.exceptions import SourceDocumentNotFoundError
from app.services.document_editing.schemas import EditResult, GeneratedArtifact, LocatedChange

DRAFT_BUCKET = "ai-nd-drafts"
REPORT_BUCKET = "ai-agent-reports"


class DocumentEditService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.locator = ChangeLocator(db)
        self.applier = ChangeApplier()
        self.diff_service = DiffService()
        self.docx_editor = DocxEditor()

    async def load_source_document(self, document_id: uuid.UUID, document_version_id: uuid.UUID | None = None) -> tuple[Document, DocumentVersion | None]:
        document = await self.db.get(Document, document_id)
        if document is None:
            raise SourceDocumentNotFoundError("Документ не найден")
        version = await self.db.get(DocumentVersion, document_version_id) if document_version_id else None
        return document, version

    async def locate_change_place(
        self,
        *,
        document_id: uuid.UUID,
        document_version_id: uuid.UUID | None,
        change_text: str,
    ) -> list[LocatedChange]:
        return await self.locator.locate(
            document_id=document_id,
            document_version_id=document_version_id,
            change_text=change_text,
        )

    async def apply_change(
        self,
        *,
        change_request_id: uuid.UUID,
        request_number: str,
        document_id: uuid.UUID,
        document_version_id: uuid.UUID | None,
        document_title: str,
        reason: str,
        release_date,
        effective_date,
        change_text: str,
        location: LocatedChange | None,
        attachments: list[str] | None = None,
        distribution_list: list[str] | None = None,
        initiator_comment: str | None = None,
    ) -> EditResult:
        document, version = await self.load_source_document(document_id, document_version_id)
        source_bytes, source_warning = self._load_editable_docx(document, version)
        operation = self.applier.classify(change_text, location.current_text if location else None)
        final_text = self.applier.apply_to_text(
            old_text=operation.old_text,
            new_text=operation.new_text,
            operation_type=operation.operation_type,
        )
        diff = self.diff_service.generate(
            section_number=location.section_number if location else None,
            old_text=operation.old_text,
            new_text=final_text,
        )

        safe_number = self._safe_filename(request_number)
        draft_filename = f"{safe_number}_project_new_revision.docx"
        notice_filename = f"{safe_number}_change_notice.docx"
        draft_object_name = f"nd-change-requests/{change_request_id}/drafts/{draft_filename}"
        notice_object_name = f"nd-change-requests/{change_request_id}/notices/{notice_filename}"

        draft_bytes = self.docx_editor.build_draft(
            title=document_title,
            request_number=request_number,
            reason=reason,
            old_text=operation.old_text,
            new_text=final_text,
            source_bytes=source_bytes,
            operation_type=operation.operation_type,
        )
        notice_bytes = self.docx_editor.build_notice(
            request_number=request_number,
            document_title=document_title,
            reason=reason,
            release_date=release_date,
            effective_date=effective_date,
            change_text=change_text,
            distribution_list=distribution_list or [],
            attachments=attachments or [],
            initiator_comment=initiator_comment,
        )
        object_storage.put_object_to_bucket(DRAFT_BUCKET, draft_object_name, draft_bytes, self.docx_editor.content_type)
        object_storage.put_object_to_bucket(REPORT_BUCKET, notice_object_name, notice_bytes, self.docx_editor.content_type)

        warnings = []
        if source_warning:
            warnings.append(source_warning)
        if operation.requires_manual_review:
            warnings.append("Тип изменения определён неуверенно, требуется проверка специалистом НТД")

        actions = [{"type": "review_diff", "title": "Проверить diff «было / стало»"}]
        if source_warning:
            actions.append({"type": "verify_editable_source", "title": "Проверить проект, сформированный без редактируемого DOCX"})

        return EditResult(
            draft_file=GeneratedArtifact(
                bucket=DRAFT_BUCKET,
                object_name=draft_object_name,
                filename=draft_filename,
                content_type=self.docx_editor.content_type,
                size=len(draft_bytes),
                warnings=[source_warning] if source_warning else [],
            ),
            notice_file=GeneratedArtifact(
                bucket=REPORT_BUCKET,
                object_name=notice_object_name,
                filename=notice_filename,
                content_type=self.docx_editor.content_type,
                size=len(notice_bytes),
            ),
            diff=diff,
            warnings=warnings,
            actions=actions,
        )

    def update_change_registration_sheet(self) -> None:
        return None

    def save_draft_file(self) -> None:
        return None

    def return_preview_data(self, result: EditResult) -> dict:
        return {
            "draft_file": result.draft_file.__dict__,
            "change_notice_file": result.notice_file.__dict__,
            "diff": [item.__dict__ for item in result.diff],
            "warnings": result.warnings,
            "actions": result.actions,
        }

    def draft_status_for_artifact(self, artifact: GeneratedArtifact) -> NdChangeDraftFileStatus:
        return (
            NdChangeDraftFileStatus.WARNING_SOURCE_NOT_EDITABLE
            if artifact.warnings
            else NdChangeDraftFileStatus.GENERATED
        )

    def _load_editable_docx(self, document: Document, version: DocumentVersion | None) -> tuple[bytes | None, str | None]:
        source = version or document
        filename = source.original_filename or document.original_filename or ""
        content_type = source.content_type or document.content_type or ""
        if PurePath(filename).suffix.lower() != ".docx" and content_type != self.docx_editor.content_type:
            return None, "Редактируемый исходник DOCX отсутствует, требуется проверка специалистом НТД"
        object_name = source.object_name or document.object_name
        bucket = source.bucket_name or document.bucket_name
        if not object_name:
            return None, "Редактируемый исходник отсутствует, требуется проверка специалистом НТД"
        if bucket and bucket != object_storage.bucket:
            return object_storage.get_object_from_bucket(bucket, object_name), None
        return object_storage.get_object(object_name), None

    def _safe_filename(self, value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Zа-яА-Я0-9_.-]+", "_", value).strip("_")
        return cleaned or "nd_change_request"
