from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.enums import (
    ConfidenceLevel,
    KnowledgeBaseSourceStatus,
    NdBuildStatus,
    NdExtractionStatus,
    NdGraphEntityType,
    NdRelationExtractionType,
    NdRelationType,
    NdStructuralDocumentStatus,
    NdStructuralDocumentType,
)
from app.schemas.common import ORMModel, Page


class NdRelationEvidenceItem(BaseModel):
    document_id: uuid.UUID | None = None
    document_code: str | None = None
    page: int | None = None
    section: str | None = None
    quote: str | None = None


class NdDepartmentFunctionItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=512)
    description: str | None = None
    source_document_ids: list[uuid.UUID] = Field(default_factory=list)


class NdProcessActionItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=512)
    description: str | None = None
    order: int | None = None


class NdProcessRoleItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    responsibilities: list[str] = Field(default_factory=list)


class NdProcessFormItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    code: str | None = None


class NdProcessSystemItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    kind: str | None = None


class NdProcessResourceItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    kind: str | None = None


class DocumentCardCreate(BaseModel):
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    file_name: str | None = None
    document_code: str | None = None
    title: str | None = None
    document_type: NdStructuralDocumentType | None = None
    version: str | None = None
    status: NdStructuralDocumentStatus = NdStructuralDocumentStatus.DRAFT
    approval_date: date | None = None
    effective_date: date | None = None
    purpose: str | None = None
    scope_text: str | None = None
    kb_parse_status: KnowledgeBaseSourceStatus | None = None
    extraction_status: NdExtractionStatus = NdExtractionStatus.PENDING
    extraction_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    raw_extracted_json: dict[str, Any] | None = None


class DocumentCardUpdate(BaseModel):
    file_name: str | None = None
    document_code: str | None = None
    title: str | None = None
    document_type: NdStructuralDocumentType | None = None
    version: str | None = None
    status: NdStructuralDocumentStatus | None = None
    approval_date: date | None = None
    effective_date: date | None = None
    purpose: str | None = None
    scope_text: str | None = None
    kb_parse_status: KnowledgeBaseSourceStatus | None = None
    extraction_status: NdExtractionStatus | None = None
    extraction_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    raw_extracted_json: dict[str, Any] | None = None


class DocumentCardRead(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    file_name: str | None
    document_code: str | None
    title: str | None
    document_type: NdStructuralDocumentType | None
    version: str | None
    status: NdStructuralDocumentStatus
    approval_date: date | None
    effective_date: date | None
    purpose: str | None
    scope_text: str | None
    kb_parse_status: KnowledgeBaseSourceStatus | None
    extraction_status: NdExtractionStatus
    extraction_confidence: Decimal | None
    raw_extracted_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class DepartmentProfileCreate(BaseModel):
    department_id: uuid.UUID
    department_name: str = Field(..., min_length=1, max_length=255)
    summary: str | None = None
    purpose: str | None = None
    functions_json: list[NdDepartmentFunctionItem] | None = None
    source_knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list)
    build_status: NdBuildStatus = NdBuildStatus.PENDING
    raw_profile_json: dict[str, Any] | None = None

    @field_validator("source_knowledge_base_ids", mode="before")
    @classmethod
    def _coerce_kb_ids(cls, value: Any) -> list[uuid.UUID]:
        if value is None:
            return []
        return [uuid.UUID(str(item)) for item in value]


class DepartmentProfileUpdate(BaseModel):
    department_name: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = None
    purpose: str | None = None
    functions_json: list[NdDepartmentFunctionItem] | None = None
    source_knowledge_base_ids: list[uuid.UUID] | None = None
    build_status: NdBuildStatus | None = None
    raw_profile_json: dict[str, Any] | None = None


class DepartmentProfileRead(ORMModel):
    id: uuid.UUID
    department_id: uuid.UUID
    department_name: str
    summary: str | None
    purpose: str | None
    functions_json: list[dict[str, Any]] | None
    source_knowledge_base_ids: list[uuid.UUID] | None
    build_status: NdBuildStatus
    raw_profile_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class ProcessCardCreate(BaseModel):
    canonical_name: str = Field(..., min_length=1, max_length=512)
    alternative_names: list[str] = Field(default_factory=list)
    description: str | None = None
    goal: str | None = None
    owner_candidate: str | None = None
    owner_confirmed: bool = False
    owner_confidence: ConfidenceLevel | None = None
    source_document_ids: list[uuid.UUID] = Field(default_factory=list)
    inputs_json: list[dict[str, Any]] | None = None
    outputs_json: list[dict[str, Any]] | None = None
    actions_json: list[NdProcessActionItem] | None = None
    roles_json: list[NdProcessRoleItem] | None = None
    forms_json: list[NdProcessFormItem] | None = None
    systems_json: list[NdProcessSystemItem] | None = None
    resources_json: list[NdProcessResourceItem] | None = None


class ProcessCardUpdate(BaseModel):
    alternative_names: list[str] | None = None
    description: str | None = None
    goal: str | None = None
    owner_candidate: str | None = None
    owner_confirmed: bool | None = None
    owner_confidence: ConfidenceLevel | None = None
    source_document_ids: list[uuid.UUID] | None = None
    inputs_json: list[dict[str, Any]] | None = None
    outputs_json: list[dict[str, Any]] | None = None
    actions_json: list[NdProcessActionItem] | None = None
    roles_json: list[NdProcessRoleItem] | None = None
    forms_json: list[NdProcessFormItem] | None = None
    systems_json: list[NdProcessSystemItem] | None = None
    resources_json: list[NdProcessResourceItem] | None = None


class ProcessCardRead(ORMModel):
    id: uuid.UUID
    canonical_name: str
    alternative_names: list[str] | None
    description: str | None
    goal: str | None
    owner_candidate: str | None
    owner_confirmed: bool
    owner_confidence: ConfidenceLevel | None
    source_document_ids: list[uuid.UUID] | None
    inputs_json: list[dict[str, Any]] | None
    outputs_json: list[dict[str, Any]] | None
    actions_json: list[dict[str, Any]] | None
    roles_json: list[dict[str, Any]] | None
    forms_json: list[dict[str, Any]] | None
    systems_json: list[dict[str, Any]] | None
    resources_json: list[dict[str, Any]] | None
    created_at: datetime
    updated_at: datetime


class NdRelationCreate(BaseModel):
    source_type: NdGraphEntityType
    source_id: uuid.UUID | None = None
    source_name: str = Field(..., min_length=1, max_length=512)
    relation_type: NdRelationType
    target_type: NdGraphEntityType
    target_id: uuid.UUID | None = None
    target_name: str = Field(..., min_length=1, max_length=512)
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    extraction_type: NdRelationExtractionType
    evidence_json: list[NdRelationEvidenceItem] = Field(default_factory=list)
    is_confirmed: bool = False


class NdRelationUpdate(BaseModel):
    source_name: str | None = Field(default=None, min_length=1, max_length=512)
    target_name: str | None = Field(default=None, min_length=1, max_length=512)
    confidence: ConfidenceLevel | None = None
    extraction_type: NdRelationExtractionType | None = None
    evidence_json: list[NdRelationEvidenceItem] | None = None
    is_confirmed: bool | None = None


class NdRelationRead(ORMModel):
    id: uuid.UUID
    source_type: NdGraphEntityType
    source_id: uuid.UUID | None
    source_name: str
    relation_type: NdRelationType
    target_type: NdGraphEntityType
    target_id: uuid.UUID | None
    target_name: str
    confidence: ConfidenceLevel
    extraction_type: NdRelationExtractionType
    evidence_json: list[dict[str, Any]] | None
    is_confirmed: bool
    created_at: datetime
    updated_at: datetime


DocumentCardPage = Page[DocumentCardRead]
ProcessCardPage = Page[ProcessCardRead]
NdRelationPage = Page[NdRelationRead]
