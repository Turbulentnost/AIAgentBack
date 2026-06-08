from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.models.enums import NdChangeLocationType, NdChangeOperationType


@dataclass(slots=True)
class LocatedChange:
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None
    section_number: str | None = None
    section_title: str | None = None
    page_number: int | None = None
    chunk_id: uuid.UUID | None = None
    location_type: NdChangeLocationType = NdChangeLocationType.BLOCK_TEXT
    current_text: str | None = None
    confidence: float = 0.0
    status: str = "candidate"


@dataclass(slots=True)
class ChangeOperationDraft:
    operation_type: NdChangeOperationType
    old_text: str | None
    new_text: str
    requires_manual_review: bool = False


@dataclass(slots=True)
class DiffItem:
    section_number: str | None
    old_text: str
    new_text: str


@dataclass(slots=True)
class GeneratedArtifact:
    bucket: str
    object_name: str
    filename: str
    content_type: str
    size: int
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EditResult:
    draft_file: GeneratedArtifact
    notice_file: GeneratedArtifact
    diff: list[DiffItem]
    warnings: list[str]
    actions: list[dict]
