from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


DispositionCode = Literal[
    "post_and_use",
    "forbid",
    "sort",
    "return",
    "rework",
    "other",
    "commission",
]

DISPOSITION_LABELS_RU: dict[str, str] = {
    "post_and_use": "оприходовать и использовать",
    "forbid": "запретить",
    "sort": "рассортировать",
    "return": "вернуть",
    "rework": "направить на доработку",
    "other": "иное",
    "commission": "комиссия",
}

ALLOWED_DISPOSITIONS: tuple[DispositionCode, ...] = (
    "post_and_use",
    "forbid",
    "sort",
    "return",
    "rework",
    "other",
    "commission",
)

TmcCategory = Literal[
    "electronics",
    "metal",
    "fasteners",
    "cable",
    "pipes",
    "flanges",
    "gaskets",
    "drawing_parts",
    "other",
]


class QualityFinding(BaseModel):
    field: str
    rule_id: str
    source_ref: str
    message: str
    severity: Literal["info", "warning", "critical"] = "critical"
    suggested_fix: str | None = None
    current_value: Any = None


class QualityDocumentRequirement(BaseModel):
    doc_type: str
    label: str
    mandatory: bool = True
    present: bool = False


class QualitySampleRule(BaseModel):
    """Правило выборки для конкретной поставки / предъявления (Прил. В / СТО-10-095)."""

    rule_id: str
    category: str
    sample_size: int | None = None
    sample_note: str
    scrap_threshold_pct: float = 15.0
    # Контекст поставки
    lot_qty: float | None = None
    presentation_ref: str | None = None
    nomenclature_ref: str | None = None
    supplier_ref: str | None = None
    supplier_quality_rating: str | float | int | None = None
    # Алгоритм
    sample_pct: float | None = None
    sample_basis: Literal[
        "3pct",
        "5pct",
        "10pct",
        "15pct",
        "20pct",
        "30pct",
        "50pct",
        "100pct",
        "1pct_rating",
        "per_package",
        "second_sample",
        "category_default",
    ] | None = None
    require_second_sample: bool = False
    second_sample_size: int | None = None


class QualityControlPayload(BaseModel):
    """§9.4 QualityControl schema (MVP subset)."""

    presentation_ref: str | None = None
    direction: str | None = None
    nomenclature_ref: str | None = None
    item_group: TmcCategory | str | None = None
    supplier_ref: str | None = None
    supplier_quality_rating: str | float | int | None = None
    control_rule_ids: list[str] = Field(default_factory=list)
    mandatory_documents: list[QualityDocumentRequirement] = Field(default_factory=list)
    sample_rule: QualitySampleRule | None = None
    sample_size: int | None = None
    measured_results: list[dict[str, Any]] = Field(default_factory=list)
    instrument_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    inspector_id: str | None = None
    quality_status: str | None = None
    nonconformity_act_ref: str | None = None
    disposition: DispositionCode | str | None = None
    reinspection_ref: str | None = None
    deadlines: dict[str, Any] = Field(default_factory=dict)
    findings: list[QualityFinding] = Field(default_factory=list)
    calculated_at: datetime | None = None


class QualityRoleOutput(BaseModel):
    actions: list[str] = Field(default_factory=list)
    findings: list[QualityFinding] = Field(default_factory=list)
    next_status: str | None = None
    next_agent: str | None = None
    quality_control: QualityControlPayload | None = None
    draft_artifacts: dict[str, Any] = Field(default_factory=dict)
    summary: str
    calculated_at: datetime | None = None


__all__ = [
    "ALLOWED_DISPOSITIONS",
    "DISPOSITION_LABELS_RU",
    "DispositionCode",
    "QualityControlPayload",
    "QualityDocumentRequirement",
    "QualityFinding",
    "QualityRoleOutput",
    "QualitySampleRule",
    "TmcCategory",
]
