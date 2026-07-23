from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PickerOutcome(StrEnum):
    ISSUE_FROM_STOCK = "issue_from_stock"
    PARTIAL_ISSUE = "partial_issue"
    DEFICIT_CONFIRMED = "deficit_confirmed"
    DISCREPANCY_RETURN = "discrepancy_return"
    FULLY_AVAILABLE = "fully_available"
    CLARIFICATION_REQUIRED = "clarification_required"


class PickerValidationIssue(BaseModel):
    code: str
    message: str
    field: str | None = None
    line_id: str | None = None
    source: Literal["case", "1c", "supply", "calculation"] = "case"
    blocking: bool = True


class PickerCaseInput(BaseModel):
    case_id: str
    case_number: str
    source_1c_ref: str
    source_number: str | None = None
    source_date: datetime | None = None
    source_status: str | None = None
    source_data_version: str | None = None
    source_synced_at: datetime | None = None
    department_1c_ref: str | None = None
    department_name: str | None = None
    warehouse_1c_ref: str | None = None
    warehouse_name: str | None = None
    organization_1c_ref: str | None = None
    initiator_name: str | None = None
    required_date: datetime | None = None
    production_order_1c_ref: str | None = None
    production_order_number: str | None = None


class PickerNeedLine(BaseModel):
    line_id: str
    nomenclature_id: str
    nomenclature_name: str
    characteristic_id: str | None = None
    characteristic_name: str | None = None
    unit: str | None = None
    requested_quantity: Decimal = Field(gt=0)
    required_date: datetime | None = None
    warehouse_id: str | None = None
    assignment_id: str | None = None
    assignment_name: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class PickerSupplyItem(BaseModel):
    supply_id: str
    source_type: Literal[
        "store_room",
        "warehouse",
        "reservation",
        "quality",
        "quarantine",
        "blocked",
    ]
    nomenclature_id: str
    characteristic_id: str | None = None
    unit: str = ""
    quantity: Decimal = Field(ge=0)
    warehouse_id: str | None = None
    assignment_id: str | None = None
    assignment_name: str | None = None
    accounting_quantity: Decimal | None = Field(default=None, ge=0)
    factual_quantity: Decimal | None = Field(default=None, ge=0)
    available_for_issue: bool = True
    reserved_for_other: bool = False
    quarantine: bool = False
    defective: bool = False
    blocked: bool = False
    suitable: bool = True
    exact_match: bool = True
    use_allowed: bool = True
    evidence_id: str | None = None


class PickerAssessmentLine(BaseModel):
    line_id: str
    nomenclature_id: str
    nomenclature_name: str
    characteristic_id: str | None = None
    characteristic_name: str | None = None
    unit: str
    requested_quantity: Decimal
    warehouse_id: str | None = None
    warehouse_name: str | None = None
    assignment_id: str | None = None
    assignment_name: str | None = None
    store_room_stock: Decimal = Decimal("0")
    warehouse_stock: Decimal = Decimal("0")
    accounting_quantity: Decimal = Decimal("0")
    factual_quantity: Decimal = Decimal("0")
    available_for_issue: Decimal = Decimal("0")
    reserved_other_quantity: Decimal = Decimal("0")
    discrepancy_quantity: Decimal = Decimal("0")
    has_discrepancy: bool = False
    confirmed_available: Decimal = Decimal("0")
    confirmed_deficit: Decimal = Decimal("0")
    quantity_to_issue: Decimal = Decimal("0")
    quantity_to_purchase: Decimal = Decimal("0")
    outcome: PickerOutcome
    recommendation: str
    issue_allowed: bool = True
    formulas: dict[str, str] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    excluded_supply: list[dict[str, Any]] = Field(default_factory=list)


class WarehousePickerOutput(BaseModel):
    schema_version: str = "1.0"
    case: PickerCaseInput
    calculated_at: datetime
    evidence_fingerprint: str
    positions: list[PickerAssessmentLine] = Field(default_factory=list)
    validation_issues: list[PickerValidationIssue] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    excluded_capabilities: list[str] = Field(default_factory=list)
    summary: str
    recommended_next_step: str
    decision_kind: Literal[
        "stock_confirmation",
        "deficit_confirmation",
        "discrepancy_return",
        "critical_acknowledgement",
        "none",
    ] = "none"
    conclusion: dict[str, Any] = Field(default_factory=dict)
