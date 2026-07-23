from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


MANDATORY_FIELD_KEYS = (
    "cfo",
    "article",
    "project",
    "date",
    "nomenclature",
    "quantity",
)

FIELD_LABELS_RU = {
    "cfo": "ЦФО",
    "article": "Статья",
    "project": "Проект",
    "date": "Дата",
    "nomenclature": "Номенклатура",
    "quantity": "Количество",
}


class OmtoMandatoryFields(BaseModel):
    cfo: str | None = None
    article: str | None = None
    project: str | None = None
    date: str | None = None
    nomenclature: str | None = None
    quantity: float | int | str | None = None


class OmtoFinding(BaseModel):
    field: str
    rule_id: str
    source_ref: str
    message: str
    severity: Literal["info", "warning", "critical"] = "critical"
    suggested_fix: str | None = None
    current_value: Any = None


class OmtoValidationIssue(BaseModel):
    code: str
    message: str
    field: str | None = None
    blocking: bool = True


class OmtoSupportManagerOutput(BaseModel):
    quality_status: Literal["ok", "incomplete", "critical"]
    findings: list[OmtoFinding] = Field(default_factory=list)
    checked_fields: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    clarification_draft: str | None = None
    summary: str
    calculated_at: datetime | None = None


__all__ = [
    "FIELD_LABELS_RU",
    "MANDATORY_FIELD_KEYS",
    "OmtoFinding",
    "OmtoMandatoryFields",
    "OmtoSupportManagerOutput",
    "OmtoValidationIssue",
]
