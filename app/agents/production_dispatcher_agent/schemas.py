from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class DispatcherOutcome(StrEnum):
    FULLY_COVERED = "fully_covered"
    RESERVE_STOCK = "reserve_stock"
    TRANSFER_PROPOSED = "transfer_proposed"
    LINK_INCOMING = "link_incoming"
    PROCUREMENT_REQUIRED = "procurement_required"
    CRITICAL_SHORTAGE = "critical_shortage"
    CLARIFICATION_REQUIRED = "clarification_required"
    ALREADY_COVERED = "already_covered"


class DispatcherUrgency(StrEnum):
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class DispatcherValidationIssue(BaseModel):
    code: str
    message: str
    field: str | None = None
    line_id: str | None = None
    source: Literal["case", "1c", "supply", "calculation"] = "case"
    blocking: bool = True


class DispatcherCaseInput(BaseModel):
    case_id: str
    case_number: str
    source_type: str
    source_1c_ref: str
    source_number: str | None = None
    source_date: datetime | None = None
    source_status: str | None = None
    source_data_version: str | None = None
    source_synced_at: datetime | None = None
    warehouse_1c_ref: str | None = None
    warehouse_name: str | None = None
    department_1c_ref: str | None = None
    department_name: str | None = None
    organization_1c_ref: str | None = None
    initiator_1c_ref: str | None = None
    initiator_name: str | None = None
    required_date: datetime | None = None
    production_order_1c_ref: str | None = None
    production_order_number: str | None = None
    source_basis_1c_ref: str | None = None
    source_basis_number: str | None = None
    stock_growth_coefficient: Decimal = Field(default=Decimal("1"), ge=0)


class DispatcherNeedLine(BaseModel):
    line_id: str
    nomenclature_id: str
    nomenclature_name: str
    characteristic_id: str | None = None
    characteristic_name: str | None = None
    unit: str | None = None
    quantity: Decimal = Field(gt=0)
    required_date: datetime | None = None
    warehouse_id: str | None = None
    minimum_stock: Decimal | None = Field(default=None, ge=0)
    maximum_stock: Decimal | None = Field(default=None, ge=0)
    reorder_point: Decimal | None = Field(default=None, ge=0)
    stock_growth_coefficient: Decimal | None = Field(default=None, ge=0)
    lead_time_days: int | None = Field(default=None, ge=0)
    daily_consumption: Decimal | None = Field(default=None, ge=0)
    production_deficit: Decimal | None = Field(default=None, ge=0)
    package_multiple: Decimal | None = Field(default=None, gt=0)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class DispatcherSupplyItem(BaseModel):
    supply_id: str
    source_type: Literal[
        "warehouse",
        "store_room",
        "in_transit",
        "in_progress",
        "supplier_order",
        "internal_transfer",
        "work_in_progress",
        "semifinished",
        "reservation",
        "existing_case",
    ]
    nomenclature_id: str
    characteristic_id: str | None = None
    unit: str = ""
    quantity: Decimal = Field(ge=0)
    warehouse_id: str | None = None
    available_at: datetime | None = None
    confirmed: bool = True
    reserved_for_other: bool = False
    incoming_control_passed: bool = True
    quarantine: bool = False
    defective: bool = False
    blocked: bool = False
    expired: bool = False
    suitable: bool = True
    exact_match: bool = True
    use_allowed: bool = True
    linked_document_number: str | None = None
    linked_document_ref: str | None = None
    evidence_id: str | None = None


class DispatcherSupplyBreakdown(BaseModel):
    source_type: str
    quantity: Decimal
    supply_ids: list[str] = Field(default_factory=list)


class DispatcherExcludedSupply(BaseModel):
    supply_id: str
    source_type: str
    quantity: Decimal
    reason: str
    evidence_id: str | None = None


class DispatcherRecommendation(BaseModel):
    method: Literal["reserve_stock", "transfer", "link_incoming", "procurement", "none"]
    quantity: Decimal = Decimal("0")
    label: str
    details: str | None = None
    requires_confirmation: bool = True


class DispatcherAssessmentLine(BaseModel):
    line_id: str
    nomenclature_id: str
    nomenclature_name: str
    characteristic_id: str | None = None
    characteristic_name: str | None = None
    unit: str
    warehouse_id: str | None = None
    minimum_stock: Decimal
    maximum_stock: Decimal
    reorder_point: Decimal
    stock_growth_coefficient: Decimal
    free_stock: Decimal = Decimal("0")
    store_room_stock: Decimal = Decimal("0")
    expected_in_transit: Decimal = Decimal("0")
    expected_in_progress: Decimal = Decimal("0")
    expected_total: Decimal = Decimal("0")
    confirmed_arrivals: Decimal = Decimal("0")
    available_other_warehouses: Decimal = Decimal("0")
    production_demand: Decimal = Decimal("0")
    stock_position: Decimal = Decimal("0")
    forecast_stock: Decimal = Decimal("0")
    below_minimum: bool = False
    below_reorder_point: bool = False
    net_deficit: Decimal = Decimal("0")
    recommended_order_quantity: Decimal = Decimal("0")
    required_date: datetime | None = None
    urgency: DispatcherUrgency = DispatcherUrgency.NORMAL
    wait_allowed: bool = True
    outcome: DispatcherOutcome
    coverage_method: str
    recommendation: str
    recommendations: list[DispatcherRecommendation] = Field(default_factory=list)
    supply_breakdown: list[DispatcherSupplyBreakdown] = Field(default_factory=list)
    excluded_supply: list[DispatcherExcludedSupply] = Field(default_factory=list)
    formulas: dict[str, str] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class ProductionDispatcherOutput(BaseModel):
    schema_version: str = "1.0"
    case: DispatcherCaseInput
    calculated_at: datetime
    evidence_fingerprint: str
    positions: list[DispatcherAssessmentLine] = Field(default_factory=list)
    validation_issues: list[DispatcherValidationIssue] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    excluded_capabilities: list[str] = Field(default_factory=list)
    summary: str
    recommended_next_step: str
    decision_kind: Literal[
        "supply_confirmation",
        "critical_acknowledgement",
        "none",
    ] = "none"
