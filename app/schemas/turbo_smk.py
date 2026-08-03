from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.models.enums import NdAcknowledgementStatus, NdValidationSeverity, NdValidationStandard
from app.schemas.common import ORMModel, Page


class NdValidationFinding(ORMModel):
    code: str
    severity: NdValidationSeverity
    standard: NdValidationStandard
    section: str | None = None
    message: str
    recommendation: str | None = None
    requirement_ref: str | None = None


class NdDocumentValidationReport(ORMModel):
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None
    overall_passed: bool
    findings: list[NdValidationFinding]
    checked_standards: list[NdValidationStandard]
    generated_at: datetime


class NdImpactAnalysisRequest(ORMModel):
    change_request_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    change_text: str | None = None


class NdImpactItem(ORMModel):
    entity_type: str
    entity_id: str | None = None
    title: str
    impact_level: str
    reason: str


class NdImpactAnalysisReport(ORMModel):
    affected_processes: list[NdImpactItem]
    affected_documents: list[NdImpactItem]
    process_owners: list[str]
    adjacent_departments: list[str]
    record_forms: list[str]
    diagrams: list[str]
    acknowledgement_targets: list[str]
    risks: list[str]
    recommendation: str
    suggested_route: list[str]


class NdAcknowledgementCreate(ORMModel):
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None
    change_request_id: uuid.UUID | None = None
    user_ids: list[uuid.UUID]
    due_at: datetime | None = None
    document_code: str | None = None
    document_name: str | None = None


class NdAcknowledgementRead(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None
    change_request_id: uuid.UUID | None
    user_id: uuid.UUID
    status: NdAcknowledgementStatus
    due_at: datetime | None
    acknowledged_at: datetime | None
    document_code: str | None
    document_name: str | None
    note: str | None
    created_at: datetime
    updated_at: datetime


class NdAcknowledgementConfirm(ORMModel):
    note: str | None = Field(default=None, max_length=2000)


NdAcknowledgementPage = Page[NdAcknowledgementRead]


class NdReportRequest(ORMModel):
    parameters: dict | None = None


class NdReportResult(ORMModel):
    kind: str
    title: str
    generated_at: datetime
    rows: list[dict]
    summary: dict | None = None


class NdVisioImportResult(ORMModel):
    filename: str
    imported: bool
    diagram_format: str
    node_count: int
    warnings: list[str]
    mermaid_preview: str | None = None


class NdBulkImportRequest(ORMModel):
    root_path: str
    department_name: str | None = None
    dry_run: bool = False


class NdBulkImportResult(ORMModel):
    scanned_files: int
    imported_cards: int
    skipped_files: int
    errors: list[str]
    dry_run: bool
