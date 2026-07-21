from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class EngineerOutcome(StrEnum):
    FULLY_COVERED = "fully_covered"
    TRANSFER_REQUIRED = "transfer_required"
    PARTIALLY_COVERED = "partially_covered"
    COVERED_BY_OPEN_ORDER = "covered_by_open_order"
    PROCUREMENT_REQUIRED = "procurement_required"
    CRITICAL_SHORTAGE = "critical_shortage"
    CLARIFICATION_REQUIRED = "clarification_required"


class EngineerCriticality(StrEnum):
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class EngineerValidationIssue(BaseModel):
    code: str
    message: str
    field: str | None = None
    line_id: str | None = None
    source: Literal["case", "1c", "specification", "supply", "calculation"] = "case"
    blocking: bool = True


class EngineerCaseInput(BaseModel):
    case_id: str
    case_number: str
    source_1c_ref: str
    source_number: str | None = None
    source_date: datetime | None = None
    source_status: str | None = None
    source_data_version: str | None = None
    source_synced_at: datetime | None = None
    initiator_1c_ref: str | None = None
    initiator_name: str | None = None
    department_1c_ref: str | None = None
    department_name: str | None = None
    organization_1c_ref: str | None = None
    warehouse_1c_ref: str | None = None
    warehouse_name: str | None = None
    priority_1c_ref: str | None = None
    required_date: datetime | None = None
    production_order_1c_ref: str | None = None
    production_order_number: str | None = None
    production_order_status: str | None = None


class EngineerNeedLine(BaseModel):
    line_id: str
    nomenclature_id: str
    nomenclature_name: str
    characteristic_id: str | None = None
    characteristic_name: str | None = None
    unit_id: str | None = None
    unit: str | None = None
    product_quantity: Decimal | None = Field(default=None, gt=0)
    direct_quantity: Decimal | None = Field(default=None, gt=0)
    required_date: datetime | None = None
    project_id: str | None = None
    production_stage_id: str | None = None
    production_stage_name: str | None = None
    warehouse_id: str | None = None
    stage_cannot_start_without_material: bool = False
    acceptable_analog_available: bool | None = None
    critical_production_order: bool = False
    section_stop_risk: bool = False
    long_lead_time: bool = False
    procurement_lead_days: int | None = Field(default=None, ge=0)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_quantity(self) -> EngineerNeedLine:
        if self.product_quantity is None and self.direct_quantity is None:
            raise ValueError("Не указано количество изделия или прямая потребность")
        return self


class ResourceSpecificationMaterial(BaseModel):
    line_id: str
    nomenclature_id: str
    nomenclature_name: str
    characteristic_id: str | None = None
    characteristic_name: str | None = None
    unit_id: str | None = None
    unit: str | None = None
    consumption_rate: Decimal = Field(gt=0)
    technological_loss_percent: Decimal = Field(default=Decimal("0"), ge=0)
    production_stage_id: str | None = None
    production_stage_name: str | None = None
    warehouse_id: str | None = None
    costing_item_id: str | None = None
    allowed_analogs: list[str] = Field(default_factory=list)
    allowed_semifinished: list[str] = Field(default_factory=list)
    material_ownership: Literal["own", "tolling", "unknown"] = "unknown"
    use_conditions: str | None = None


class ResourceSpecification(BaseModel):
    specification_id: str
    name: str
    version: str | None = None
    status: str
    deletion_mark: bool = False
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    product_id: str
    product_characteristic_id: str | None = None
    approved: bool = False
    completeness_score: int = 0
    production_stage_id: str | None = None
    materials: list[ResourceSpecificationMaterial] = Field(default_factory=list)
    evidence_id: str | None = None


class EngineerSupplyItem(BaseModel):
    supply_id: str
    source_type: Literal[
        "warehouse",
        "store_room",
        "semifinished",
        "in_transit",
        "supplier_order",
        "internal_transfer",
        "work_in_progress",
        "reservation",
        "existing_case",
        "production_plan",
    ]
    nomenclature_id: str
    characteristic_id: str | None = None
    unit: str
    quantity: Decimal = Field(ge=0)
    warehouse_id: str | None = None
    available_at: datetime | None = None
    confirmed: bool = True
    reserved_for_other: bool = False
    safety_stock_forbidden: bool = False
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


class EngineerSupplyBreakdown(BaseModel):
    source_type: str
    quantity: Decimal
    supply_ids: list[str] = Field(default_factory=list)


class EngineerExcludedSupply(BaseModel):
    supply_id: str
    source_type: str
    quantity: Decimal
    reason: str
    evidence_id: str | None = None


class EngineerCriticalImpact(BaseModel):
    production_order: str | None = None
    production_stage: str | None = None
    shortage_start_date: datetime | None = None
    possible_stop_date: datetime | None = None
    unprovided_product_quantity: Decimal | None = None
    consequence: str
    recommended_priority: str


class EngineerAssessmentLine(BaseModel):
    line_id: str
    nomenclature_id: str
    nomenclature_name: str
    characteristic_id: str | None = None
    characteristic_name: str | None = None
    unit: str
    production_order: str | None = None
    production_stage: str | None = None
    product_quantity: Decimal
    consumption_rate: Decimal
    technological_loss_percent: Decimal
    gross_requirement: Decimal
    free_stock: Decimal = Decimal("0")
    available_other_warehouses: Decimal = Decimal("0")
    warehouse_stock_before: Decimal = Decimal("0")
    warehouse_stock_used: Decimal = Decimal("0")
    warehouse_stock_remaining: Decimal = Decimal("0")
    confirmed_arrivals: Decimal = Decimal("0")
    total_available_supply: Decimal = Decimal("0")
    net_requirement: Decimal = Decimal("0")
    required_date: datetime
    criticality: EngineerCriticality
    outcome: EngineerOutcome
    coverage_method: str
    recommendation: str
    specification_id: str
    specification_version: str | None = None
    supply_breakdown: list[EngineerSupplyBreakdown] = Field(default_factory=list)
    excluded_supply: list[EngineerExcludedSupply] = Field(default_factory=list)
    linked_documents: list[dict[str, str]] = Field(default_factory=list)
    critical_impact: EngineerCriticalImpact | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ProductionPreparationEngineerOutput(BaseModel):
    schema_version: str = "1.0"
    case: EngineerCaseInput
    calculated_at: datetime
    evidence_fingerprint: str
    specifications: list[ResourceSpecification] = Field(default_factory=list)
    positions: list[EngineerAssessmentLine] = Field(default_factory=list)
    validation_issues: list[EngineerValidationIssue] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    excluded_capabilities: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    summary: str
    recommended_next_step: str


__all__ = [
    "EngineerAssessmentLine",
    "EngineerCaseInput",
    "EngineerCriticalImpact",
    "EngineerCriticality",
    "EngineerExcludedSupply",
    "EngineerNeedLine",
    "EngineerOutcome",
    "EngineerSupplyBreakdown",
    "EngineerSupplyItem",
    "EngineerValidationIssue",
    "ProductionPreparationEngineerOutput",
    "ResourceSpecification",
    "ResourceSpecificationMaterial",
]
