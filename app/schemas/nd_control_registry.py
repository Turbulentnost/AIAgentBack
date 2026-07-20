from __future__ import annotations

import uuid
from datetime import date
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import (
    NdTemplateClassificationStatus,
    NdConfidentialityLevel,
    NdDocumentCardStatus,
    NdDocumentType,
    NdQmsLevel,
    NdTemplateType,
)
from app.schemas.common import ORMModel, Page
from app.utils.nd_template_classification import ND_TEMPLATE_TYPE_LABELS


class NdControlPermissionsRead(BaseModel):
    can_manage_departments: bool
    can_access_agent: bool
    can_manage_templates: bool = False
    can_upload_template_documents: bool = False
    can_reanalyze_departments: bool = False
    can_view_change_journal: bool = False
    is_process_management_specialist: bool = False


class NdControlDepartmentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    knowledge_base_ids: list[uuid.UUID] = Field(..., min_length=1)
    auto_start_analysis: bool = True


class NdControlDepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    sort_order: int | None = None


class NdControlDepartmentKnowledgeBasesUpdate(BaseModel):
    knowledge_base_ids: list[uuid.UUID] = Field(..., min_length=1)


class NdControlDepartmentRead(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    sort_order: int
    is_active: bool
    created_by_user_id: uuid.UUID | None
    knowledge_bases_count: int = 0
    cards_count: int = 0
    documents_count: int = 0
    processes_count: int = 0
    pending_review_count: int = 0
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list)
    analysis_status: str | None = None
    analysis_progress_percent: int | None = None


class NdDocumentCardRead(ORMModel):
    id: uuid.UUID
    department_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    knowledge_base_source_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    document_code: str | None
    document_name: str | None
    document_type: NdDocumentType | None
    qms_level: NdQmsLevel | None
    version: str | None
    status: NdDocumentCardStatus
    approval_date: date | None
    effective_date: date | None
    process_owner: str | None
    author: str | None
    reviewer: str | None
    approver: str | None
    owner_department: str | None
    scope: str | None
    related_processes: list[str] | None
    related_departments: list[str] | None
    related_documents: list[str] | None
    normative_references: list[str] | None
    record_forms: list[str] | None
    retention_period: str | None
    original_storage_location: str | None
    electronic_storage_location: str | None
    has_process_diagram: bool
    has_acknowledgement_sheet: bool
    acknowledgement_targets: list[str] | None
    confidentiality_level: NdConfidentialityLevel | None
    change_history: list[dict[str, Any]] | None
    approval_history: list[dict[str, Any]] | None
    attachments: list[str] | None
    archived_versions: list[str] | None


class NdDocumentCardUpdate(BaseModel):
    document_code: str | None = None
    document_name: str | None = None
    document_type: NdDocumentType | None = None
    qms_level: NdQmsLevel | None = None
    version: str | None = None
    status: NdDocumentCardStatus | None = None
    approval_date: date | None = None
    effective_date: date | None = None
    process_owner: str | None = None
    author: str | None = None
    reviewer: str | None = None
    approver: str | None = None
    owner_department: str | None = None
    scope: str | None = None
    related_processes: list[str] | None = None
    related_departments: list[str] | None = None
    related_documents: list[str] | None = None
    normative_references: list[str] | None = None
    record_forms: list[str] | None = None
    retention_period: str | None = None
    original_storage_location: str | None = None
    electronic_storage_location: str | None = None
    has_process_diagram: bool | None = None
    has_acknowledgement_sheet: bool | None = None
    acknowledgement_targets: list[str] | None = None
    confidentiality_level: NdConfidentialityLevel | None = None
    change_history: list[dict[str, Any]] | None = None
    approval_history: list[dict[str, Any]] | None = None
    attachments: list[str] | None = None
    archived_versions: list[str] | None = None


class NdTemplateTypeOption(BaseModel):
    value: NdTemplateType
    label: str


class NdControlTemplateSourceRead(BaseModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    knowledge_base_name: str | None = None
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    document_title: str | None = None
    original_filename: str | None = None
    processing_status: str | None = None
    already_registered: bool = False


class NdControlTemplateCreate(BaseModel):
    template_type: NdTemplateType
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    sort_order: int | None = None


class NdControlTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class NdControlTemplateRead(ORMModel):
    id: uuid.UUID
    template_type: NdTemplateType
    template_type_label: str
    name: str
    title: str
    description: str | None
    sort_order: int
    is_active: bool
    created_by_user_id: uuid.UUID | None
    knowledge_bases_count: int = 0
    documents_count: int = 0
    created_at: datetime
    updated_at: datetime


class NdControlTemplateDocumentRead(ORMModel):
    id: uuid.UUID
    template_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    knowledge_base_source_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    detected_template_type: NdTemplateType | None
    classification_confidence: float | None
    classification_status: NdTemplateClassificationStatus
    classified_at: datetime | None
    classified_by: str | None
    created_at: datetime
    updated_at: datetime


NdDocumentCardPage = Page[NdDocumentCardRead]
NdControlTemplatePage = Page[NdControlTemplateRead]
