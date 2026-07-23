from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import ConfidentialityLevel, DocumentCardStatus, QmsDocumentKind, QmsLevel
from app.schemas.common import ORMModel


class DocumentCardBase(BaseModel):
    document_code: str = Field(..., max_length=64)
    document_name: str = Field(..., max_length=512)
    document_type: QmsDocumentKind
    qms_level: QmsLevel
    version: str | None = Field(default=None, max_length=64)
    status: DocumentCardStatus = DocumentCardStatus.DRAFT
    approval_date: date | None = None
    effective_date: date | None = None
    process_owner: str | None = Field(default=None, max_length=255)
    author: str | None = Field(default=None, max_length=255)
    reviewer: str | None = Field(default=None, max_length=255)
    approver: str | None = Field(default=None, max_length=255)
    owner_department: str | None = Field(default=None, max_length=255)
    scope: str | None = None
    related_processes: list[str] = Field(default_factory=list)
    related_departments: list[str] = Field(default_factory=list)
    related_documents: list[str] = Field(default_factory=list)
    normative_references: list[str] = Field(default_factory=list)
    record_forms: list[str] = Field(default_factory=list)
    retention_period: str | None = Field(default=None, max_length=128)
    original_storage_location: str | None = Field(default=None, max_length=512)
    electronic_storage_location: str | None = Field(default=None, max_length=512)
    has_process_diagram: bool = False
    has_acknowledgement_sheet: bool = False
    acknowledgement_targets: list[str] = Field(default_factory=list)
    confidentiality_level: ConfidentialityLevel = ConfidentialityLevel.PUBLIC
    change_history: list[dict] = Field(default_factory=list)
    approval_history: list[dict] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)
    archived_versions: list[str] = Field(default_factory=list)


class DocumentCardCreate(DocumentCardBase):
    document_id: uuid.UUID


class DocumentCardUpdate(BaseModel):
    document_code: str | None = Field(default=None, max_length=64)
    document_name: str | None = Field(default=None, max_length=512)
    document_type: QmsDocumentKind | None = None
    qms_level: QmsLevel | None = None
    version: str | None = Field(default=None, max_length=64)
    status: DocumentCardStatus | None = None
    approval_date: date | None = None
    effective_date: date | None = None
    process_owner: str | None = Field(default=None, max_length=255)
    author: str | None = Field(default=None, max_length=255)
    reviewer: str | None = Field(default=None, max_length=255)
    approver: str | None = Field(default=None, max_length=255)
    owner_department: str | None = Field(default=None, max_length=255)
    scope: str | None = None
    related_processes: list[str] | None = None
    related_departments: list[str] | None = None
    related_documents: list[str] | None = None
    normative_references: list[str] | None = None
    record_forms: list[str] | None = None
    retention_period: str | None = Field(default=None, max_length=128)
    original_storage_location: str | None = Field(default=None, max_length=512)
    electronic_storage_location: str | None = Field(default=None, max_length=512)
    has_process_diagram: bool | None = None
    has_acknowledgement_sheet: bool | None = None
    acknowledgement_targets: list[str] | None = None
    confidentiality_level: ConfidentialityLevel | None = None
    change_history: list[dict] | None = None
    approval_history: list[dict] | None = None
    attachments: list[str] | None = None
    archived_versions: list[str] | None = None


class DocumentCardRead(ORMModel, DocumentCardBase):
    id: uuid.UUID
    document_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DocumentCardBootstrapResult(BaseModel):
    created: int
    skipped: int
    total_documents: int


class DocumentCardImportFolderRequest(BaseModel):
    folder_path: str = Field(..., min_length=3, max_length=1024)
    recursive: bool = True
    dry_run: bool = False
    is_knowledge_base: bool = True


class DocumentCardImportItem(BaseModel):
    source_path: str
    relative_path: str
    status: str
    document_id: uuid.UUID | None = None
    card_id: uuid.UUID | None = None
    document_code: str | None = None
    document_name: str | None = None
    message: str | None = None


class DocumentCardFolderImportResult(BaseModel):
    folder_path: str
    total_files: int
    created: int
    skipped: int
    failed: int
    dry_run: bool = False
    items: list[DocumentCardImportItem] = Field(default_factory=list)


class DocumentCardFolderScanResult(BaseModel):
    folder_path: str
    total_files: int
    files: list[dict] = Field(default_factory=list)
