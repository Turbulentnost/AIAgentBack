from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DiagramDetailLevel(str, Enum):
    COMPACT = "compact"
    STANDARD = "standard"
    DETAILED = "detailed"


class SmkEvidenceItem(BaseModel):
    document_id: str | None = None
    document_code: str | None = None
    page: int | None = None
    section: str | None = None
    quote: str | None = None


def evidence_to_dicts(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


class ProcessEffectivenessCriterionItem(BaseModel):
    name: str
    measurement_method: str | None = None
    reporting_period: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ProcessResourceItem(BaseModel):
    name: str
    type: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ProcessRiskItem(BaseModel):
    risk: str
    consequence: str | None = None
    control_measure: str | None = None
    responsible: str | None = None
    related_action_id: str | None = None
    related_action_title: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ProcessDocumentationArchiveItem(BaseModel):
    document: str
    storage_place: str | None = None
    responsible: str | None = None
    retention_term: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ProcessApplicationItem(BaseModel):
    name: str
    code: str | None = None
    description: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ProcessChangeRegistrationItem(BaseModel):
    title: str
    description: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ProcessIssueAcquaintanceItem(BaseModel):
    title: str
    description: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
