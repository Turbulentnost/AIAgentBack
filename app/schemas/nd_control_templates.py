from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import NdTemplateClassificationStatus, NdTemplateType
from app.schemas.common import ORMModel, Page


class NdTemplateClassificationStats(BaseModel):
    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    needs_review: int = 0


class NdControlTemplateRead(ORMModel):
    id: uuid.UUID
    name: str
    template_type: NdTemplateType
    template_type_label: str
    description: str | None
    sort_order: int
    is_active: bool
    created_by_user_id: uuid.UUID | None
    documents_count: int
    knowledge_bases_count: int
    classification_stats: NdTemplateClassificationStats
    created_at: datetime
    updated_at: datetime


class NdControlTemplateDetail(NdControlTemplateRead):
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list)


class NdControlTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class NdControlTemplateKnowledgeBasesUpdate(BaseModel):
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list)


class NdControlTemplateDocumentCreate(BaseModel):
    knowledge_base_source_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_source_or_document(self):
        if self.knowledge_base_source_id is None and self.document_id is None:
            raise ValueError("Укажите knowledge_base_source_id или document_id")
        if self.knowledge_base_source_id is not None and self.document_id is not None:
            raise ValueError("Укажите только один идентификатор: knowledge_base_source_id или document_id")
        return self


class NdControlTemplateDocumentUpdate(BaseModel):
    confirm_detected_type: bool = False


class NdControlTemplateDocumentRead(ORMModel):
    id: uuid.UUID
    template_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    knowledge_base_source_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    detected_template_type: NdTemplateType | None
    detected_template_type_label: str | None = None
    classification_confidence: float | None
    classification_status: NdTemplateClassificationStatus
    classified_at: datetime | None
    classified_by: str | None
    metadata: dict | None = None
    knowledge_base_name: str | None = None
    document_title: str | None = None
    original_filename: str | None = None
    created_at: datetime
    updated_at: datetime


NdControlTemplatePage = Page[NdControlTemplateRead]
NdControlTemplateDocumentPage = Page[NdControlTemplateDocumentRead]
