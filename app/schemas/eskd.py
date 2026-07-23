from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import EskdDocumentKind, EskdRegistrationStatus
from app.schemas.common import ORMModel
from app.schemas.document import DocumentRead
from app.schemas.document_card import DocumentCardRead


class EskdModuleInfoRead(BaseModel):
    module: str = "eskd"
    version: str
    agent_slug: str
    capabilities: list[str]
    supported_document_kinds: list[str]


class EskdDocumentUploadRegisterRequest(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    designation: str | None = Field(default=None, max_length=128, description="Обозначение документа по ЕСКД")
    document_kind: EskdDocumentKind = EskdDocumentKind.OTHER
    owner_department: str | None = Field(default=None, max_length=255)
    nd_control_department_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    notes: str | None = None
    relative_path: str | None = None
    start_processing: bool = True
    is_knowledge_base: bool = True


class EskdRegisterExistingRequest(BaseModel):
    designation: str | None = Field(default=None, max_length=128)
    document_kind: EskdDocumentKind = EskdDocumentKind.OTHER
    owner_department: str | None = Field(default=None, max_length=255)
    nd_control_department_id: uuid.UUID | None = None
    notes: str | None = None
    start_processing: bool = True


class EskdDocumentRegistrationRead(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    qms_document_card_id: uuid.UUID | None
    nd_control_department_id: uuid.UUID | None
    registered_by_user_id: uuid.UUID | None
    agent_slug: str
    designation: str | None
    document_kind: EskdDocumentKind
    status: EskdRegistrationStatus
    owner_department: str | None
    notes: str | None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")
    celery_task_id: str | None
    created_at: datetime
    updated_at: datetime


class EskdUploadRegisterResponse(BaseModel):
    registration: EskdDocumentRegistrationRead
    document: DocumentRead
    document_card: DocumentCardRead | None = None
    processing_queued: bool = False
    celery_task_id: str | None = None


class EskdRegistrationListResponse(BaseModel):
    items: list[EskdDocumentRegistrationRead]
    total: int
    page: int
    size: int


class EskdCheckResultRead(BaseModel):
    code: str
    title: str
    passed: bool
    severity: str
    message: str
    gost_reference: str | None = None
    details: dict = Field(default_factory=dict)


class EskdValidationReportRead(BaseModel):
    passed: bool
    score: float
    summary: str
    errors_count: int = 0
    warnings_count: int = 0
    checks: list[EskdCheckResultRead]
    document_id: str | None = None
    registration_id: str | None = None
    designation: str | None = None
    document_kind: str | None = None
    text_available: bool = False
    validated_at: str
    registration_status: EskdRegistrationStatus | None = None
