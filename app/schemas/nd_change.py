from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import (
    NdChangeApprovalStatus,
    NdChangeDraftFileStatus,
    NdChangeLocationStatus,
    NdChangeLocationType,
    NdChangeOperationStatus,
    NdChangeOperationType,
    NdChangeRequestStatus,
    NdChangeResultStatus,
)
from app.schemas.common import ORMModel


class NdChangeRequestCreate(BaseModel):
    reason: str = Field(..., min_length=1)
    release_date: date | None = None
    effective_date: date | None = None
    change_text: str = Field(..., min_length=1)
    department_id: uuid.UUID | None = None
    assumed_document_id: uuid.UUID | None = None
    assumed_document_code: str | None = None
    attachments: list[str] = Field(default_factory=list)
    distribution_list: list[str] = Field(default_factory=list)
    initiator_comment: str | None = None
    metadata: dict[str, Any] | None = None


class NdChangeRequestUpdate(BaseModel):
    reason: str | None = None
    release_date: date | None = None
    effective_date: date | None = None
    change_text: str | None = None
    department_id: uuid.UUID | None = None
    attachments: list[str] | None = None
    distribution_list: list[str] | None = None
    initiator_comment: str | None = None
    metadata: dict[str, Any] | None = None


class NdChangeRequestRead(ORMModel):
    id: uuid.UUID
    number: str
    reason: str
    release_date: date | None
    effective_date: date | None
    change_text: str
    initiator_user_id: uuid.UUID | None
    department_id: uuid.UUID | None
    status: NdChangeRequestStatus
    selected_document_id: uuid.UUID | None
    selected_document_version_id: uuid.UUID | None
    detection_confidence: float | None
    requires_manual_document_selection: bool
    requires_manual_location_selection: bool
    metadata_: dict[str, Any] | None = Field(default=None, serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class NdChangeCandidateDocumentRead(ORMModel):
    id: uuid.UUID
    change_request_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None
    score: float
    rank: int
    match_reason: str | None
    matched_fragments: list | None
    is_selected: bool
    created_at: datetime
    updated_at: datetime
    document_title: str | None = None
    document_code: str | None = None


class NdChangeSelectDocument(BaseModel):
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None


class NdChangeFindLocationRequest(BaseModel):
    document_id: uuid.UUID | None = None
    document_version_id: uuid.UUID | None = None


class NdChangeTargetLocationRead(ORMModel):
    id: uuid.UUID
    change_request_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None
    section_number: str | None
    section_title: str | None
    page_number: int | None
    chunk_id: uuid.UUID | None
    location_type: NdChangeLocationType
    current_text: str | None
    confidence: float | None
    status: NdChangeLocationStatus
    created_at: datetime
    updated_at: datetime


class NdChangeApplyRequest(BaseModel):
    location_id: uuid.UUID | None = None
    approval_user_ids: list[uuid.UUID] = Field(default_factory=list)
    mark_user_reviewed: bool = False


class NdChangeOperationRead(ORMModel):
    id: uuid.UUID
    change_request_id: uuid.UUID
    target_location_id: uuid.UUID | None
    operation_type: NdChangeOperationType
    old_text: str | None
    new_text: str | None
    diff: list | None
    status: NdChangeOperationStatus
    requires_manual_review: bool
    created_at: datetime
    updated_at: datetime


class NdChangeDraftFileRead(ORMModel):
    id: uuid.UUID
    change_request_id: uuid.UUID
    document_id: uuid.UUID | None
    source_document_version_id: uuid.UUID | None
    draft_bucket: str
    draft_object_name: str
    original_filename: str | None
    generated_filename: str
    file_type: str
    status: NdChangeDraftFileStatus
    file_size: int | None
    created_at: datetime


class NdChangeApprovalParticipantRead(ORMModel):
    id: uuid.UUID
    approval_route_id: uuid.UUID
    user_id: uuid.UUID | None
    role_name: str | None
    approval_order: int
    status: NdChangeApprovalStatus
    comment: str | None
    approved_at: datetime | None


class NdChangeApprovalRouteRead(ORMModel):
    id: uuid.UUID
    change_request_id: uuid.UUID
    status: NdChangeApprovalStatus
    created_by_user_id: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    participants: list[NdChangeApprovalParticipantRead] = Field(default_factory=list)


class NdChangeResultRead(ORMModel):
    id: uuid.UUID
    change_request_id: uuid.UUID
    agent_id: str | None
    status: NdChangeResultStatus
    summary: str | None
    confidence: float | None
    selected_document_id: uuid.UUID | None
    draft_file_id: uuid.UUID | None
    change_notice_file_id: uuid.UUID | None
    warnings: list | None
    actions: list | None
    metadata_: dict[str, Any] | None = Field(default=None, serialization_alias="metadata")
    created_at: datetime


class NdChangePreviewRead(BaseModel):
    request: NdChangeRequestRead
    candidates: list[NdChangeCandidateDocumentRead]
    target_locations: list[NdChangeTargetLocationRead]
    operations: list[NdChangeOperationRead]
    draft_files: list[NdChangeDraftFileRead]
    approval_routes: list[NdChangeApprovalRouteRead]
    result: NdChangeResultRead | None = None


class NdChangeAgentStructuredResult(BaseModel):
    status: str
    change_request_id: uuid.UUID
    selected_document: dict[str, Any] | None
    confidence: float | None
    target_locations: list[dict[str, Any]]
    diff: list[dict[str, Any]]
    related_documents: list[dict[str, Any]]
    draft_file: dict[str, Any] | None
    change_notice_file: dict[str, Any] | None
    approval_recipients: list[dict[str, Any]]
    warnings: list[str]
    requires_user_review: bool = True
