from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Supplier(BaseModel):
    supplier_id: str
    name: str
    tax_id: str | None = None
    source: Literal["1c", "internal", "web"] = "internal"
    categories: list[str] = Field(default_factory=list)
    quality_rating: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    delivery_rating: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    commercial_rating: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    is_active: bool = True
    contacts: dict[str, str] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    url: str | None = None
    city: str | None = None
    unit_price: Decimal | None = Field(default=None, ge=0)
    approx_cost: Decimal | None = Field(default=None, ge=0)
    rating: Decimal | None = Field(default=None, ge=0, le=100)
    abc_class: Literal["A", "B", "C"] | None = None
    abc_spend_share: Decimal | None = Field(default=None, ge=0, le=1)


class NomenclatureSearchItem(BaseModel):
    """One case position / nomenclature to search suppliers for."""

    nomenclature_id: str | None = None
    nomenclature_name: str | None = None
    query: str | None = Field(default=None, min_length=1, max_length=500)
    # Pre-matched bank / prior-search suppliers (service fills; clients may omit).
    existing_suppliers: list[Supplier] = Field(default_factory=list)


class SupplierSearchRequest(BaseModel):
    query: str | None = Field(default=None, min_length=2, max_length=500)
    category: str | None = Field(default=None, max_length=255)
    limit: int = Field(default=10, ge=1, le=50)
    allow_web_fallback: bool = True
    # Manual Find-suppliers button: bank-only seeds must not block Edge/Bing web search.
    force_web: bool = False
    mode: Literal["auto", "manual_web"] | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    nomenclatures: list[NomenclatureSearchItem] = Field(default_factory=list)
    # UI alarm-clock budget: stop between page fetches when exceeded (30s–10min).
    timeout_seconds: float | None = Field(default=None, ge=30, le=600)

    @property
    def is_manual_web(self) -> bool:
        return bool(self.force_web or self.mode == "manual_web")


class NomenclatureSupplierResult(BaseModel):
    """Supplier search results scoped to a single nomenclature."""

    nomenclature_id: str | None = None
    nomenclature_name: str | None = None
    query: str
    suppliers: list[Supplier] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)
    web_fallback_used: bool = False


class SupplierSearchResult(BaseModel):
    query: str
    suppliers: list[Supplier]
    sources_used: list[str]
    web_fallback_used: bool = False
    nomenclature_results: list[NomenclatureSupplierResult] = Field(default_factory=list)
    operation_id: str | None = None
    pending: bool = False
    status: Literal["completed", "running", "failed"] = "completed"
    message: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class PurchaseBatch(BaseModel):
    batch_no: int
    line_id: str
    quantity: float = 0
    required_date: str | None = None
    supplier_id: str | None = None
    supplier_name: str | None = None
    coverage_source: Literal["warehouse", "supplier", "mixed", "none"] | str = "none"
    unit_price: float | None = None
    planned_arrival: str | None = None
    supplier_lead_days: int | None = None
    supplier_ship_date: str | None = None
    meets_deadline: bool | None = None


class LineScheduleUpdateRequest(BaseModel):
    lead_days: int | None = Field(default=None, ge=0, le=3650)
    ship_date: date | None = None
    required_date: date | None = None
    batch_no: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _require_input(self) -> LineScheduleUpdateRequest:
        if self.lead_days is None and self.ship_date is None and self.required_date is None:
            raise ValueError("Укажите lead_days, ship_date или required_date")
        return self


class FulfillmentStatusUpdateRequest(BaseModel):
    fulfillment_status: Literal[
        "no_supplier",
        "payment",
        "delivery",
        "otk_presentation",
        "posting",
        "completed",
    ]
    idempotency_key: str | None = Field(default=None, max_length=255)


class RFQLine(BaseModel):
    line_id: str
    nomenclature_id: str | None = None
    description: str
    quantity: Decimal = Field(gt=0)
    unit: str
    required_date: date | None = None


class RFQDraftRequest(BaseModel):
    supplier_ids: list[str] = Field(min_length=1)
    lines: list[RFQLine] = Field(min_length=1)
    response_deadline: datetime | None = None
    delivery_address: str | None = None
    terms: list[str] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=1, max_length=255)


class RFQDraft(BaseModel):
    rfq_id: str
    supplier_ids: list[str]
    lines: list[RFQLine]
    subject: str
    body: str
    status: Literal["draft"] = "draft"
    created_at: datetime


class QuoteLine(BaseModel):
    line_id: str
    unit_price: Decimal = Field(ge=0)
    quantity: Decimal = Field(gt=0)
    delivery_days: int = Field(ge=0)
    compliant: bool = True


class SupplierQuote(BaseModel):
    quote_id: str
    supplier_id: str
    rfq_id: str | None = None
    currency: str = Field(default="RUB", min_length=3, max_length=3)
    lines: list[QuoteLine] = Field(min_length=1)
    valid_until: date | None = None
    payment_terms: str | None = None
    warranty_months: int = Field(default=0, ge=0)
    quality_score: Decimal = Field(default=Decimal("50"), ge=0, le=100)
    risk_score: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    received_at: datetime | None = None

    @property
    def total(self) -> Decimal:
        return sum((line.unit_price * line.quantity for line in self.lines), Decimal("0"))


class QuoteSubmission(BaseModel):
    quote: SupplierQuote
    idempotency_key: str = Field(min_length=1, max_length=255)

    @model_validator(mode="before")
    @classmethod
    def accept_flat_quote(cls, value: Any) -> Any:
        if isinstance(value, dict) and "quote" not in value:
            payload = dict(value)
            idempotency_key = payload.pop("idempotency_key", None)
            return {"quote": payload, "idempotency_key": idempotency_key}
        return value


class ComparisonWeights(BaseModel):
    price: Decimal = Field(default=Decimal("0.45"), ge=0)
    delivery: Decimal = Field(default=Decimal("0.25"), ge=0)
    quality: Decimal = Field(default=Decimal("0.20"), ge=0)
    risk: Decimal = Field(default=Decimal("0.10"), ge=0)

    @model_validator(mode="after")
    def validate_sum(self) -> ComparisonWeights:
        if self.price + self.delivery + self.quality + self.risk <= 0:
            raise ValueError("At least one comparison weight must be positive")
        return self


class QuoteScore(BaseModel):
    quote_id: str
    supplier_id: str
    total: Decimal
    price_score: Decimal
    delivery_score: Decimal
    quality_score: Decimal
    risk_score: Decimal
    final_score: Decimal
    rank: int = 0
    eligible: bool = True
    reasons: list[str] = Field(default_factory=list)


class QuoteComparison(BaseModel):
    weights: ComparisonWeights
    scores: list[QuoteScore]
    recommended_quote_id: str | None = None
    generated_at: datetime


class RecommendationRequest(BaseModel):
    supplier_id: str = Field(min_length=1, max_length=128)
    quote_id: str = Field(min_length=1, max_length=128)
    rationale: str | None = Field(default=None, max_length=2000)
    supplier_selection_approval_id: str | None = Field(default=None, max_length=128)
    price_approval_id: str | None = Field(default=None, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255)


class RecommendationRecord(BaseModel):
    recommendation_id: str
    supplier_id: str
    quote_id: str
    total: Decimal
    currency: str
    score: Decimal | None = None
    rationale: str | None = None
    status: Literal["approval_required", "approved"]
    supplier_selection_approval_id: str | None = None
    price_approval_id: str | None = None
    requires_human_approval: bool = True
    payment_execution_allowed: bool = False
    created_at: datetime


class ApprovalRecord(BaseModel):
    approval_id: str
    operation: Literal[
        "select_supplier",
        "approve_price",
        "send_rfq",
        "create_supplier_order",
        "update_supplier_order",
        "record_shipment",
    ]
    status: Literal["requested", "approved", "rejected"]
    comment: str | None = None
    actor_user_id: str | None = None
    created_at: datetime


class ApprovalRequest(BaseModel):
    approval_id: str | None = Field(default=None, max_length=128)
    operation: Literal[
        "select_supplier",
        "approve_price",
        "send_rfq",
        "create_supplier_order",
        "update_supplier_order",
        "record_shipment",
    ]
    status: Literal["requested", "approved", "rejected"] = "requested"
    comment: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)


class ShipmentEvent(BaseModel):
    event_id: str
    event_type: Literal["ordered", "dispatched", "in_transit", "delayed", "received"]
    occurred_at: datetime
    supplier_id: str | None = None
    tracking_number: str | None = None
    details: dict = Field(default_factory=dict)


class ShipmentEventRequest(BaseModel):
    event: ShipmentEvent
    approval_id: str
    idempotency_key: str = Field(min_length=1, max_length=255)


class Nonconformity(BaseModel):
    nonconformity_id: str
    shipment_event_id: str | None = None
    description: str = Field(min_length=1)
    severity: Literal["minor", "major", "critical"]
    quantity_affected: Decimal | None = Field(default=None, gt=0)
    evidence: list[str] = Field(default_factory=list)
    created_at: datetime


class NonconformityRequest(BaseModel):
    nonconformity: Nonconformity
    idempotency_key: str = Field(min_length=1, max_length=255)


class OperationStatus(BaseModel):
    operation_id: str
    case_id: str | None = None
    operation: str
    status: Literal[
        "draft",
        "running",
        "completed",
        "approval_required",
        "approved",
        "executed",
        "rejected",
        "failed",
    ]
    approval_id: str | None = None
    external_ref: str | None = None
    error: str | None = None
    updated_at: datetime
    # Live search / Qwen stages from in-memory progress buffer (soft, optional).
    thoughts: list[str] = Field(default_factory=list)


class LineAmountEntry(BaseModel):
    line_id: str = Field(min_length=1, max_length=128)
    unit_price: Decimal | None = Field(default=None, ge=0)
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="RUB", min_length=3, max_length=3)
    # manual = saved by manager; po = healed from purchase-order drafts.
    source: str | None = Field(default=None, max_length=32)


class LineAmountsUpdateRequest(BaseModel):
    lines: list[LineAmountEntry] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)


class WorkspaceSummary(BaseModel):
    """KPI for procurement manager workspace (scoped to manager queue only).

    - uncovered_orders_count: queue cases with tone «Полностью необеспечен»
      (must be ≤ total_orders_count = len(left-list cases))
    - uncovered_positions_count: uncovered line items in that same queue
      (must be ≤ positions_count; not qty sum, not global DB count)
    - active_suppliers_count: seed bank active suppliers (100)
    - nomenclature_count: kept for compat (= uncovered_positions_count)
    """

    uncovered_orders_count: int = 0
    active_suppliers_count: int = 0
    uncovered_positions_count: int = 0
    nomenclature_count: int = 0
    total_orders_count: int = 0
    ready_orders_count: int = 0
    attention_orders_count: int = 0
    positions_count: int = 0
    need_quantity_total: Decimal = Decimal("0")
    bank_quantity_total: Decimal = Decimal("0")
    warehouses_count: int = 0
    generated_at: datetime


class MaterialBankTotals(BaseModel):
    warehouses_count: int = 0
    suppliers_count: int = 0
    stock_lines_count: int = 0
    warehouse_quantity_total: Decimal = Decimal("0")
    supplier_quantity_total: Decimal = Decimal("0")
    bank_quantity_total: Decimal = Decimal("0")


class NomenclaturePriceBound(BaseModel):
    nomenclature_id: str
    nomenclature_name: str | None = None
    price_min: Decimal
    price_max: Decimal
    offer_count: int = 0
    suppliers_count: int = 0


class MaterialBankResponse(BaseModel):
    warehouses: list[dict[str, Any]] = Field(default_factory=list)
    stock: list[dict[str, Any]] = Field(default_factory=list)
    suppliers: list[dict[str, Any]] = Field(default_factory=list)
    materials: list[dict[str, Any]] = Field(default_factory=list)
    price_bounds: list[NomenclaturePriceBound] = Field(default_factory=list)
    totals: MaterialBankTotals


class TopSupplierOffer(BaseModel):
    """Ranked supplier offer for a nomenclature need (deterministic score)."""

    rank: int = Field(ge=1)
    supplier_id: str
    supplier_name: str
    nomenclature_id: str | None = None
    nomenclature_name: str | None = None
    unit_price: Decimal
    available_qty: Decimal
    coverable_qty: Decimal
    coverage_ratio: Decimal
    coverage_cost: Decimal
    total_cost: Decimal | None = None
    overpay: Decimal | None = None
    price_score: Decimal | None = None
    coverage_score: Decimal | None = None
    score: Decimal
    reason: str = ""
    unit: str = "шт"
    lead_time_days: int | None = None
    meets_deadline: bool | None = None
    deadline_status: Literal["ok", "miss", "unknown"] | None = None
    deadline_risk: bool = False
    optimization_rank: int | None = None
    optimization_reason: str | None = None
    source: str | None = None


class UsedSupplierPart(BaseModel):
    """Supplier actually used by allocation for a line / nomenclature remainder."""

    supplier_id: str
    supplier_name: str
    quantity: Decimal = Decimal("0")
    unit_price: Decimal | None = None


class SupplierOffersResponse(BaseModel):
    nomenclature_id: str
    nomenclature_name: str | None = None
    need_qty: Decimal
    unit: str = "шт"
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    score_formula: str
    top_suppliers: list[TopSupplierOffer] = Field(default_factory=list)


class AllPositionsRow(BaseModel):
    """Aggregated nomenclature row for «Все позиции» mode."""

    nomenclature_id: str | None = None
    nomenclature_name: str | None = None
    unit: str = "шт"
    quantity: Decimal = Decimal("0")
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    avg_unit_price: Decimal | None = None
    estimated_amount: Decimal | None = None
    amount: Decimal | None = None
    overpay: Decimal | None = None
    amount_source: str = "—"
    amount_formula: str | None = None
    currency: str = "RUB"
    coverage_source: Literal["warehouse", "supplier", "mixed", "none"] | None = None
    coverage_source_label: str | None = None
    from_warehouse: Decimal | None = None
    from_supplier: Decimal | None = None
    positions_count: int = 0
    has_manual_override: bool = False
    top_suppliers: list[TopSupplierOffer] = Field(default_factory=list)
    used_suppliers: list[UsedSupplierPart] = Field(default_factory=list)
    required_date: date | datetime | str | None = None


class AllPositionsResponse(BaseModel):
    rows: list[AllPositionsRow] = Field(default_factory=list)
    total_estimated_amount: Decimal | None = None
    currency: str = "RUB"
    amount_formula: str
    price_formula: str | None = None
    score_formula: str | None = None


class OrderCoverageStatus(BaseModel):
    tone: Literal["ready", "attention", "uncovered"]
    label: str
    covered_count: int = 0
    positions_count: int = 0
    uncovered_positions_count: int = 0
    has_suppliers: bool = False


class AllocationSummary(BaseModel):
    total_orders_count: int = 0
    uncovered_orders_count: int = 0
    ready_orders_count: int = 0
    attention_orders_count: int = 0
    uncovered_positions_count: int = 0
    positions_count: int = 0
    need_quantity_total: Decimal = Decimal("0")
    covered_quantity_total: Decimal = Decimal("0")
    bank_quantity_total: Decimal = Decimal("0")
    active_suppliers_count: int = 0
    warehouses_count: int = 0


class AllocationResult(BaseModel):
    cases: list[dict[str, Any]] = Field(default_factory=list)
    lines: list[dict[str, Any]] = Field(default_factory=list)
    by_nomenclature: list[dict[str, Any]] = Field(default_factory=list)
    summary: AllocationSummary
    price_formula: str | None = None


class PurchaseOrderLine(BaseModel):
    line_id: str
    nomenclature_id: str
    description: str
    quantity: Decimal = Field(gt=0)
    unit: str = "шт"
    unit_price: Decimal = Field(ge=0)
    delivery_days: int = Field(default=7, ge=0)

    @property
    def line_total(self) -> Decimal:
        return self.quantity * self.unit_price


class PurchaseOrderDraft(BaseModel):
    po_id: str
    supplier_id: str
    supplier_name: str
    lines: list[PurchaseOrderLine] = Field(min_length=1)
    currency: str = "RUB"
    total: Decimal = Decimal("0")
    source_quote_id: str | None = None
    subject: str
    body: str
    status: Literal["draft", "approved_draft"] = "draft"
    payment_execution_allowed: bool = False
    created_at: datetime


class PurchaseOrderDraftRequest(BaseModel):
    supplier_id: str = Field(min_length=1, max_length=128)
    lines: list[PurchaseOrderLine] = Field(min_length=1)
    source_quote_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)


class AgentRunRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    allow_web_fallback: bool = True
    query: str | None = None


class AgentResumeRequest(BaseModel):
    action: Literal[
        "approve_shortlist",
        "approve_rfq_draft",
        "approve_order_draft",
        "reject",
    ]
    comment: str | None = Field(default=None, max_length=2000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)


class AgentStatus(BaseModel):
    case_id: str
    stage: str | None = None
    status: str | None = None
    paused_for_human: bool = False
    interrupt_type: str | None = None
    recommendation: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None = None
    cost_estimate: dict[str, Any] | None = None
    rfq_draft: dict[str, Any] | None = None
    purchase_order_draft: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    kpi_flags: dict[str, Any] = Field(default_factory=dict)
    candidates_count: int = 0
    payment_execution_allowed: bool = False


class StrategyRunRequest(BaseModel):
    """Queue-level supply strategy run (multi-case)."""

    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    allow_web_fallback: bool = True
    query: str | None = None
    case_ids: list[str] = Field(default_factory=list)


class StrategyResumeRequest(BaseModel):
    action: Literal[
        "approve_shortlist",
        "approve_policy",
        "approve_rfq_draft",
        "approve_order_draft",
        "reject",
    ]
    comment: str | None = Field(default=None, max_length=2000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)


class StrategyStatus(BaseModel):
    """Queue strategy status: waves, supply_policy, multi-PO drafts."""

    run_id: str | None = None
    stage: str | None = None
    status: str | None = None
    paused_for_human: bool = False
    interrupt_type: str | None = None
    case_ids: list[str] = Field(default_factory=list)
    waves: dict[str, Any] | None = None
    supply_policy: dict[str, Any] | None = None
    explanation: dict[str, Any] | None = None
    cost_estimate: dict[str, Any] | None = None
    purchase_order_drafts: list[dict[str, Any]] = Field(default_factory=list)
    queue_plan_summary: dict[str, Any] | None = None
    supplier_diversity: list[dict[str, Any]] = Field(default_factory=list)
    kpi_flags: dict[str, Any] = Field(default_factory=dict)
    candidates_count: int = 0
    payment_execution_allowed: bool = False


__all__ = [
    "AgentResumeRequest",
    "AgentRunRequest",
    "AgentStatus",
    "StrategyResumeRequest",
    "StrategyRunRequest",
    "StrategyStatus",
    "AllocationResult",
    "AllocationSummary",
    "AllPositionsResponse",
    "AllPositionsRow",
    "ApprovalRecord",
    "ApprovalRequest",
    "ComparisonWeights",
    "LineAmountEntry",
    "LineAmountsUpdateRequest",
    "MaterialBankResponse",
    "MaterialBankTotals",
    "NomenclaturePriceBound",
    "Nonconformity",
    "NonconformityRequest",
    "NomenclatureSearchItem",
    "NomenclatureSupplierResult",
    "OperationStatus",
    "OrderCoverageStatus",
    "PurchaseOrderDraft",
    "PurchaseOrderDraftRequest",
    "PurchaseOrderLine",
    "QuoteComparison",
    "QuoteScore",
    "QuoteSubmission",
    "RecommendationRecord",
    "RecommendationRequest",
    "RFQDraft",
    "RFQDraftRequest",
    "RFQLine",
    "ShipmentEvent",
    "ShipmentEventRequest",
    "Supplier",
    "SupplierOffersResponse",
    "SupplierQuote",
    "SupplierSearchRequest",
    "SupplierSearchResult",
    "TopSupplierOffer",
    "UsedSupplierPart",
    "WorkspaceSummary",
]
