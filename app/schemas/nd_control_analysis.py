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
    document_type_label: str | None = None
    document_type_confidence: str | None = None
    document_level: str | None = None
    document_level_label: str | None = None
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


class ProcessSourceDocumentItem(BaseModel):
    document_id: uuid.UUID
    document_code: str | None = None
    title: str | None = None
    display_name: str
    document_type: str | None = None
    extraction_status: str | None = None
    extraction_status_label: str | None = None


class ProcessOwnerDisplay(BaseModel):
    candidate: str | None = None
    confirmed: bool = False
    confidence: str | None = None
    confidence_label: str | None = None
    status_label: str
    reason: str | None = None


class ProcessActionDisplay(BaseModel):
    name: str
    performer: str | None = None
    controller: str | None = None
    system_or_resource: str | None = None
    evidence_label: str | None = None


class ProcessRelationsSummary(BaseModel):
    total: int = 0
    confirmed: int = 0
    unconfirmed: int = 0
    without_evidence: int = 0


class DepartmentProcessListItem(BaseModel):
    process_id: uuid.UUID
    name: str
    canonical_name: str
    description: str | None = None
    goal: str | None = None
    owner: ProcessOwnerDisplay
    owner_candidate: str | None = None
    owner_confirmed: bool = False
    owner_confidence: str | None = None
    owner_confidence_label: str | None = None
    owner_status_label: str | None = None
    source_documents: list[ProcessSourceDocumentItem] = Field(default_factory=list)
    source_document_names: list[str] = Field(default_factory=list)
    source_documents_count: int = 0
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    actions: list[ProcessActionDisplay] = Field(default_factory=list)
    action_names: list[str] = Field(default_factory=list)
    forms: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    systems_preview: str = "—"
    relations_count: int = 0
    relations_summary: ProcessRelationsSummary = Field(default_factory=ProcessRelationsSummary)
    forms_count: int = 0
    systems_count: int = 0
    needs_review: bool = False
    pending_relations_count: int = 0


class DepartmentProcessPage(BaseModel):
    items: list[DepartmentProcessListItem]
    total: int
    page: int
    size: int


class RelationEntityDisplay(BaseModel):
    type: str
    type_label: str
    id: str | None = None
    name: str


class RelationEvidenceDisplay(BaseModel):
    label: str
    document_code: str | None = None
    section: str | None = None
    quote: str | None = None


class DepartmentRelationListItem(BaseModel):
    relation_id: uuid.UUID
    source_type: str
    source_type_label: str
    source_id: uuid.UUID | None = None
    source_display_name: str
    source: RelationEntityDisplay
    relation_type: str
    relation_type_label: str
    relation: dict[str, str]
    target_type: str
    target_type_label: str
    target_id: uuid.UUID | None = None
    target_display_name: str
    target: RelationEntityDisplay
    confidence: str
    confidence_label: str
    extraction_type: str
    extraction_type_label: str
    confirmation_status: str
    confirmation_status_label: str
    is_confirmed: bool
    review_status: str
    review_status_label: str
    evidence_summary: str | None = None
    evidence_json: list[dict[str, Any]] = Field(default_factory=list)
    evidence: RelationEvidenceDisplay
    relation_description: str
    is_weak_relation: bool = False
    is_service_relation: bool = False
    is_primary_relation: bool = False
    has_evidence: bool = False
    requires_review: bool = False
    can_bulk_approve: bool = False
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
    confidence_label: str | None = None
    evidence: dict[str, Any] | None = None


class ReviewDocumentItem(BaseModel):
    document_card_id: uuid.UUID
    document_id: uuid.UUID
    document_code: str | None = None
    title: str | None = None
    reason: str | None = None
    extraction_status: str


class BulkApproveRelationsRequest(BaseModel):
    relation_ids: list[uuid.UUID] = Field(default_factory=list)


class BulkApproveRelationsResponse(BaseModel):
    approved: list[uuid.UUID] = Field(default_factory=list)
    skipped: list[uuid.UUID] = Field(default_factory=list)


class DepartmentReviewPendingRead(BaseModel):
    process_owners: list[ReviewProcessOwnerItem] = Field(default_factory=list)
    relations: list[DepartmentRelationListItem] = Field(default_factory=list)
    important_relations: list[DepartmentRelationListItem] = Field(default_factory=list)
    relations_without_evidence: list[DepartmentRelationListItem] = Field(default_factory=list)
    weak_relations: list[DepartmentRelationListItem] = Field(default_factory=list)
    extraction_errors: list[ReviewDocumentItem] = Field(default_factory=list)
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


class ProcessUmlResponse(BaseModel):
    process_id: uuid.UUID
    uml_type: str = "mermaid_activity"
    uml_code: str
    cached: bool = False
