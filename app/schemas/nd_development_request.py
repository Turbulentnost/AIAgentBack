from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field

from app.models.enums import NdDevelopmentRequestKind, NdDevelopmentRequestStatus, QmsDocumentKind
from app.schemas.common import ORMModel, Page


class NdDevelopmentRequestCreate(ORMModel):
    kind: NdDevelopmentRequestKind
    document_kind: QmsDocumentKind | None = None
    title: str = Field(min_length=3, max_length=512)
    justification: str = Field(min_length=3)
    process_description: str | None = None
    process_owner: str | None = None
    developer_department: str | None = None
    interested_departments: list[str] | None = None
    similar_documents: list[str] | None = None
    scope: str | None = None
    target_effective_date: date | None = None
    needs_process_diagram: bool = False
    needs_introduction_order: bool = False
    needs_implementation_plan: bool = False
    acknowledgement_targets: list[str] | None = None
    base_document_id: uuid.UUID | None = None
    base_document_version_id: uuid.UUID | None = None
    version_reason: str | None = None


class NdDevelopmentRequestRead(ORMModel):
    id: uuid.UUID
    number: str
    kind: NdDevelopmentRequestKind
    status: NdDevelopmentRequestStatus
    document_kind: QmsDocumentKind | None
    title: str
    justification: str
    process_description: str | None
    process_owner: str | None
    developer_department: str | None
    interested_departments: list | None
    similar_documents: list | None
    scope: str | None
    target_effective_date: date | None
    needs_process_diagram: bool
    needs_introduction_order: bool
    needs_implementation_plan: bool
    acknowledgement_targets: list | None
    base_document_id: uuid.UUID | None
    base_document_version_id: uuid.UUID | None
    version_reason: str | None
    duplicate_check_result: dict | None
    package_completeness: dict | None
    initiator_user_id: uuid.UUID | None
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NdDevelopmentDuplicateCheckRead(ORMModel):
    request_id: uuid.UUID
    matches: list[dict]
    recommendation: str


class NdDevelopmentPackageCheckRead(ORMModel):
    request_id: uuid.UUID
    is_complete: bool
    missing_items: list[str]
    warnings: list[str]


NdDevelopmentRequestPage = Page[NdDevelopmentRequestRead]
