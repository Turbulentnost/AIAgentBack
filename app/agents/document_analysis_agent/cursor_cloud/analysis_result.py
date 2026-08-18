"""Контракт output/analysis_result.json для облачного агента Cursor.

Поля дашбордов совпадают с тем, что уже кладёт analyze-excel
в dashboard snapshot и отдаёт GET …/dashboard-latest.
Обёртку снимка (user_id, saved_at, refresh_*) Cursor не пишет — её делает Авион.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agents.document_analysis_agent.dashboard_snapshot import (
    coverage_dashboard_has_data,
)

SCHEMA_VERSION = 1
ANALYSIS_RESULT_SCHEMA_ID = "aveon.cursor.analysis_result.v1"

FILE_ROLES = (
    "specification",
    "stock",
    "production_schedule",
    "detailed_production_schedule",
    "shipment_schedule",
    "other",
)
RISK_LEVELS = ("low", "medium", "high", "critical")
COVERAGE_STATUSES = ("none", "green", "yellow", "red")
PERIOD_KEYS = ("day", "week", "month", "custom")
ROW_KINDS = ("header", "group", "task", "empty")
PRIORITIES = ("urgent", "today", "week")
LOGISTICS_STAGE_KEYS = (
    "loading_dispatch",
    "msk_arrival",
    "customs_clearance",
    "rostov_arrival",
)

FileRole = Literal[
    "specification",
    "stock",
    "production_schedule",
    "detailed_production_schedule",
    "shipment_schedule",
    "other",
]
RiskLevel = Literal["low", "medium", "high", "critical"]
CoverageStatus = Literal["none", "green", "yellow", "red"]
PeriodKey = Literal["day", "week", "month", "custom"]
RowKind = Literal["header", "group", "task", "empty"]
Priority = Literal["urgent", "today", "week"]


class AnalysisResultError(ValueError):
    """JSON не прошёл контракт — дашборды из него строить нельзя."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class AnalysisIssue(_StrictModel):
    code: str = ""
    message: str
    filename: str | None = None
    role: FileRole | None = None


class FileRoleRef(_StrictModel):
    filename: str
    role: FileRole
    source: str = "upload"


class LogisticsRiskItem(_StrictModel):
    nomenclature: str
    supplier: str | None = None
    quantity: float = 0
    moscow_date: str = ""
    milestone_date: str = ""
    sheet: str = ""
    window_start: str = ""
    window_end: str = ""
    days_remaining: int = 0
    risk_ratio: float = 0
    risk_level: RiskLevel = "critical"


class LogisticsRiskStage(_StrictModel):
    key: str
    label: str
    items: list[LogisticsRiskItem] = Field(default_factory=list)


class LogisticsRiskBoard(_StrictModel):
    as_of: str | None = None
    stages: list[LogisticsRiskStage] = Field(default_factory=list)


class CoverageTiles(_StrictModel):
    all: int = 0
    green: int = 0
    yellow: int = 0
    red: int = 0
    plan_total: float = 0
    fact_total: float = 0
    covered_total: float = 0
    green_plan_total: float = 0
    yellow_plan_total: float = 0
    red_plan_total: float = 0
    green_covered_total: float = 0
    yellow_covered_total: float = 0
    red_covered_total: float = 0
    shortfall_total: float = 0
    shortfall_count: int = 0
    optional: int = 0
    optional_plan_total: float = 0
    optional_covered_total: float = 0


class CoverageMaterialLine(_StrictModel):
    name: str
    plan: float = 0
    stock: float = 0
    expected: float = 0
    shortage: float = 0
    materialKind: str = ""
    materialKindLabel: str = ""
    materialKindConfidence: str = ""
    materialKindReason: str = ""
    optional: bool = False


class CoverageProductRow(_StrictModel):
    name: str
    plan: float = 0
    fact: float = 0
    covered: float = 0
    status: CoverageStatus = "none"
    assemblableQty: float = 0
    materials: list[CoverageMaterialLine] = Field(default_factory=list)
    shortages: list[CoverageMaterialLine] = Field(default_factory=list)


class CoverageNomenclatureRow(_StrictModel):
    name: str
    plan: float = 0
    fact: float = 0
    covered: float = 0
    available: float = 0
    status: CoverageStatus = "none"
    materialKind: str = ""
    materialKindLabel: str = ""
    materialKindConfidence: str = ""
    materialKindReason: str = ""
    optional: bool = False


class CoverageSide(_StrictModel):
    rows: list[Any] = Field(default_factory=list)
    tiles: CoverageTiles = Field(default_factory=CoverageTiles)


class CoveragePeriod(_StrictModel):
    key: PeriodKey
    label: str
    days: list[str] = Field(default_factory=list)
    products: CoverageSide = Field(default_factory=CoverageSide)
    nomenclatures: CoverageSide = Field(default_factory=CoverageSide)


class CoverageDashboard(_StrictModel):
    as_of: str
    schedule_month: str = ""
    default_period: Literal["day", "week", "month"] = "day"
    default_analysis_mode: str = "conditional"
    periods: dict[str, CoveragePeriod]


class TaskDashboardMeta(_StrictModel):
    as_of: str = ""
    week_period: str = ""
    week_in_period: bool = False
    task_count: int = 0
    urgent_count: int = 0
    today_count: int = 0
    week_count: int = 0


class TaskResultEval(_StrictModel):
    status: str
    comment: str | None = None
    error: str | None = None


class TaskDashboard(_StrictModel):
    values: list[list[str]] = Field(default_factory=list)
    row_priorities: list[Priority | None] = Field(default_factory=list)
    row_kinds: list[RowKind] = Field(default_factory=list)
    meta: TaskDashboardMeta = Field(default_factory=TaskDashboardMeta)
    result_texts: dict[str, str] = Field(default_factory=dict)
    result_evals: dict[str, TaskResultEval] = Field(default_factory=dict)


class ShiftAssignmentFile(_StrictModel):
    valid_date: str
    file_name: str = "сменное_задание_закупки.xlsx"
    file_base64: str | None = None


class CoverageRebuildPlan(_StrictModel):
    product: str
    daily_qty: dict[str, float] = Field(default_factory=dict)
    daily_fact: dict[str, float] = Field(default_factory=dict)


class CoverageRebuildMerged(_StrictModel):
    nomenclature: str
    by_product: dict[str, float] = Field(default_factory=dict)
    stock: float = 0
    daily_demand: dict[str, float] = Field(default_factory=dict)
    daily_demand_fact: dict[str, float] = Field(default_factory=dict)
    daily_receipts: dict[str, float] = Field(default_factory=dict)
    monthly_demand: dict[str, Any] = Field(default_factory=dict)
    monthly_receipts: dict[str, Any] = Field(default_factory=dict)
    coverage_material_kind: str = ""
    coverage_material_label: str = ""
    coverage_material_confidence: str = ""
    coverage_material_reason: str = ""


class CoverageRebuild(_StrictModel):
    version: int = 1
    as_of: str
    schedule_month: str = ""
    day_keys: list[str] = Field(default_factory=list)
    spec_eligible_products: list[str] = Field(default_factory=list)
    plans: list[CoverageRebuildPlan] = Field(default_factory=list)
    merged: list[CoverageRebuildMerged] = Field(default_factory=list)


class CursorAnalysisResult(_StrictModel):
    """То, что Cursor обязан записать в output/analysis_result.json."""

    schema_id: str = ANALYSIS_RESULT_SCHEMA_ID
    schema_version: int = SCHEMA_VERSION
    as_of: str
    roles: list[FileRoleRef] = Field(default_factory=list)
    logistics_risks: LogisticsRiskBoard = Field(default_factory=LogisticsRiskBoard)
    coverage_dashboard: CoverageDashboard | None = None
    coverage_rebuild: CoverageRebuild | None = None
    task_dashboard: TaskDashboard | None = None
    shift_assignment: ShiftAssignmentFile | None = None
    errors: list[AnalysisIssue] = Field(default_factory=list)
    warnings: list[AnalysisIssue] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _schema_version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(f"ожидается schema_version={SCHEMA_VERSION}")
        return value

    @field_validator("as_of")
    @classmethod
    def _iso_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("as_of должен быть датой YYYY-MM-DD") from exc
        return value

    @model_validator(mode="after")
    def _must_have_payload_or_errors(self) -> CursorAnalysisResult:
        if has_dashboard_payload(self) or self.errors:
            return self
        raise ValueError(
            "нужен coverage_dashboard, logistics_risks.stages, "
            "task_dashboard или непустой errors"
        )


def has_dashboard_payload(result: CursorAnalysisResult) -> bool:
    if coverage_dashboard_has_data(result.coverage_dashboard.model_dump() if result.coverage_dashboard else None):
        return True
    if any(stage.items for stage in result.logistics_risks.stages):
        return True
    if result.task_dashboard and any(
        kind == "task" for kind in result.task_dashboard.row_kinds
    ):
        return True
    return False


def parse_analysis_result(payload: dict[str, Any] | str | bytes) -> CursorAnalysisResult:
    """Разбирает и проверяет JSON Cursor. При ошибке — AnalysisResultError."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise AnalysisResultError(f"analysis_result.json не JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnalysisResultError("analysis_result.json должен быть объектом")
    try:
        return CursorAnalysisResult.model_validate(payload)
    except Exception as exc:
        raise AnalysisResultError(f"analysis_result.json не прошёл контракт: {exc}") from exc


def analysis_result_to_snapshot_blocks(result: CursorAnalysisResult) -> dict[str, Any]:
    """Блоки для save_dashboard_snapshot — без обёртки Авиона."""
    return {
        "logistics_risks": result.logistics_risks.model_dump(),
        "coverage_dashboard": (
            result.coverage_dashboard.model_dump() if result.coverage_dashboard else None
        ),
        "coverage_rebuild": (
            result.coverage_rebuild.model_dump() if result.coverage_rebuild else None
        ),
        "task_dashboard": (
            result.task_dashboard.model_dump() if result.task_dashboard else None
        ),
        "shift_assignment": (
            result.shift_assignment.model_dump() if result.shift_assignment else None
        ),
    }
