from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import DepartmentAnalysisRunStatus, DepartmentAnalysisStep
from app.schemas.common import ORMModel
from app.schemas.nd_control_registry import NdControlDepartmentRead


class DepartmentAnalysisRunRead(ORMModel):
    id: uuid.UUID
    department_id: uuid.UUID
    status: DepartmentAnalysisRunStatus
    current_step: DepartmentAnalysisStep
    progress_percent: int
    total_knowledge_bases: int
    total_documents: int
    processed_documents: int
    skipped_documents: int
    failed_documents: int
    needs_review_documents: int
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    summary_json: dict[str, Any] | None


class NdControlDepartmentCreateResponse(BaseModel):
    department: NdControlDepartmentRead
    analysis_run: DepartmentAnalysisRunRead | None = None


class DepartmentAnalysisStartRequest(BaseModel):
    force_reextract: bool = False


class DepartmentAnalysisStatusRead(BaseModel):
    department_id: uuid.UUID
    run_id: uuid.UUID | None
    status: DepartmentAnalysisRunStatus | None
    current_step: DepartmentAnalysisStep | None
    progress_percent: int
    total_documents: int
    processed_documents: int
    skipped_documents: int
    failed_documents: int
    needs_review_documents: int
    message: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


class DepartmentSummaryRead(BaseModel):
    department_id: uuid.UUID
    department_name: str
    analysis_status: str | None
    knowledge_bases: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_bases_count: int = 0
    documents_count: int = 0
    document_cards_count: int = 0
    processes_count: int = 0
    relations_count: int = 0
    pending_review_count: int = 0
    last_analysis_at: datetime | None = None
    last_analysis_run: DepartmentAnalysisRunRead | None = None


class DepartmentKnowledgeBaseSummaryRead(BaseModel):
    id: str
    name: str
    description: str | None = None
    documents_count: int = 0
    processed_count: int = 0
    failed_count: int = 0
    status: str = "pending"


class DepartmentDocumentCardListItem(BaseModel):
    document_card_id: uuid.UUID
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    file_name: str | None = None
    document_code: str | None = None
    title: str | None = None
    document_type: str | None = None
    version: str | None = None
    status: str | None = None
    extraction_status: str
    extraction_confidence: str | None = None
    processes_count: int = 0
    relations_count: int = 0
    needs_review_count: int = 0
    updated_at: datetime | None = None
    purpose: str | None = None


class DepartmentDocumentCardPage(BaseModel):
    items: list[DepartmentDocumentCardListItem]
    total: int
    page: int
    size: int


class DepartmentProcessListItem(BaseModel):
    process_id: uuid.UUID
    canonical_name: str
    description: str | None = None
    goal: str | None = None
    owner_candidate: str | None = None
    owner_confirmed: bool = False
    owner_confidence: str | None = None
    source_documents_count: int = 0
    relations_count: int = 0
    forms_count: int = 0
    systems_count: int = 0
    needs_review: bool = False
    pending_relations_count: int = 0


class DepartmentProcessPage(BaseModel):
    items: list[DepartmentProcessListItem]
    total: int
    page: int
    size: int


class DepartmentRelationListItem(BaseModel):
    relation_id: uuid.UUID
    source_type: str
    source_name: str
    relation_type: str
    relation_type_label: str
    target_type: str
    target_name: str
    confidence: str
    extraction_type: str
    is_confirmed: bool
    review_status: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class DepartmentRelationPage(BaseModel):
    items: list[DepartmentRelationListItem]
    total: int
    page: int
    size: int


class ReviewProcessOwnerItem(BaseModel):
    process_id: uuid.UUID
    process_name: str
    owner_candidate: str | None = None
    confidence: str | None = None
    evidence: dict[str, Any] | None = None


class ReviewDocumentItem(BaseModel):
    document_card_id: uuid.UUID
    document_id: uuid.UUID
    document_code: str | None = None
    title: str | None = None
    reason: str | None = None
    extraction_status: str


class DepartmentReviewPendingRead(BaseModel):
    process_owners: list[ReviewProcessOwnerItem] = Field(default_factory=list)
    relations: list[DepartmentRelationListItem] = Field(default_factory=list)
    documents: list[ReviewDocumentItem] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)


class DepartmentAnalysisRunListItem(BaseModel):
    run_id: uuid.UUID
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: DepartmentAnalysisRunStatus
    total_documents: int = 0
    processed_documents: int = 0
    skipped_documents: int = 0
    failed_documents: int = 0
    needs_review_documents: int = 0
    processes_created: int = 0
    relations_created: int = 0
    duration_seconds: int | None = None
    error_message: str | None = None


class DepartmentAnalysisRunPage(BaseModel):
    items: list[DepartmentAnalysisRunListItem]
    total: int
    page: int
    size: int


class ConfirmProcessOwnerRequest(BaseModel):
    owner_name: str | None = None
