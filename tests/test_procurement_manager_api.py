from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from app.agents.procurement_manager_agent.schemas import (
    ApprovalRequest,
    Nonconformity,
    NonconformityRequest,
    QuoteSubmission,
    RecommendationRecord,
    RecommendationRequest,
    RFQDraftRequest,
    ShipmentEvent,
    ShipmentEventRequest,
    SupplierSearchRequest,
)
from app.api import deps as api_deps
from app.api.v1.endpoints import procurement_manager as endpoint_module
from app.main import app

PREFIX = "/api/v1/procurement/role-agents/procurement_logistics_agent"


def test_estimate_report_content_disposition_is_latin1_safe() -> None:
    header = endpoint_module._attachment_content_disposition(
        "estimate_ЗП-DEMO-0024.xlsx"
    )
    header.encode("latin-1")
    assert "filename*=UTF-8''" in header
    assert "ЗП" not in header.split("filename*=")[0]
    assert "DEMO-0024.xlsx" in header


def test_procurement_manager_api_surface_is_registered() -> None:
    paths = app.openapi()["paths"]
    assert f"{PREFIX}/dashboard" in paths
    assert f"{PREFIX}/workspace-summary" in paths
    assert f"{PREFIX}/cases/{{case_id}}" in paths
    assert "put" in paths[f"{PREFIX}/cases/{{case_id}}/line-amounts"]
    assert "get" in paths[f"{PREFIX}/cases/{{case_id}}/estimate-report"]
    assert "post" in paths[f"{PREFIX}/sync-from-1c"]
    assert "post" in paths[f"{PREFIX}/cases/{{case_id}}/supplier-search"]
    assert "post" in paths[f"{PREFIX}/cases/{{case_id}}/rfqs/draft"]
    assert "post" in paths[f"{PREFIX}/cases/{{case_id}}/rfq-drafts"]
    assert "post" in paths[f"{PREFIX}/cases/{{case_id}}/quotes"]
    assert "get" in paths[f"{PREFIX}/cases/{{case_id}}/comparison"]
    assert "post" in paths[f"{PREFIX}/cases/{{case_id}}/recommendation"]
    assert "post" in paths[f"{PREFIX}/cases/{{case_id}}/approvals"]
    assert "post" in paths[f"{PREFIX}/cases/{{case_id}}/shipment-events"]
    assert "post" in paths[f"{PREFIX}/cases/{{case_id}}/nonconformity"]
    assert "post" in paths[f"{PREFIX}/cases/{{case_id}}/agent/run"]
    assert "post" in paths[f"{PREFIX}/cases/{{case_id}}/agent/resume"]
    assert "get" in paths[f"{PREFIX}/cases/{{case_id}}/agent/status"]
    assert "post" in paths[f"{PREFIX}/strategy/run"]
    assert "post" in paths[f"{PREFIX}/strategy/resume"]
    assert "get" in paths[f"{PREFIX}/strategy/status"]
    assert "post" in paths[f"{PREFIX}/cases/{{case_id}}/purchase-order-drafts"]
    assert "get" in paths[f"{PREFIX}/cases/{{case_id}}/purchase-order-drafts"]
    assert "get" in paths[f"{PREFIX}/cases/{{case_id}}/purchase-order-drafts/{{po_id}}"]
    assert "get" in paths[f"{PREFIX}/cases/{{case_id}}/operations/{{operation_id}}"]
    assert "get" in paths[f"{PREFIX}/operations/{{operation_id}}"]
    assert "get" in paths["/api/v1/procurement/operations/{operation_id}"]
    assert (
        "201"
        in paths[f"{PREFIX}/cases/{{case_id}}/rfqs/draft"]["post"]["responses"]
    )
    assert (
        "201"
        in paths[f"{PREFIX}/cases/{{case_id}}/recommendation"]["post"]["responses"]
    )


def test_supplier_search_body_is_optional() -> None:
    operation = app.openapi()["paths"][f"{PREFIX}/cases/{{case_id}}/supplier-search"]["post"]
    assert operation.get("requestBody", {}).get("required") is not True
    assert SupplierSearchRequest().query is None


def test_normal_mutation_payloads_validate_without_422() -> None:
    rfq = RFQDraftRequest.model_validate(
        {
            "supplier_ids": ["supplier-1"],
            "lines": [
                {
                    "line_id": "line-1",
                    "description": "Steel",
                    "quantity": "10",
                    "unit": "kg",
                }
            ],
            "idempotency_key": "rfq-1",
        }
    )
    assert rfq.idempotency_key == "rfq-1"

    quote = QuoteSubmission.model_validate(
        {
            "quote_id": "quote-1",
            "supplier_id": "supplier-1",
            "currency": "RUB",
            "lines": [
                {
                    "line_id": "line-1",
                    "unit_price": "100",
                    "quantity": "10",
                    "delivery_days": 7,
                }
            ],
            "idempotency_key": "quote-1-submit",
        }
    )
    assert quote.quote.quote_id == "quote-1"

    recommendation = RecommendationRequest(
        supplier_id="supplier-1",
        quote_id="quote-1",
        idempotency_key="recommendation-1",
    )
    assert recommendation.supplier_selection_approval_id is None

    approval = ApprovalRequest(
        operation="select_supplier",
        idempotency_key="approval-request-1",
    )
    assert approval.approval_id is None

    shipment = ShipmentEventRequest(
        event=ShipmentEvent(
            event_id="shipment-1",
            event_type="in_transit",
            occurred_at=datetime.now(UTC),
        ),
        approval_id="approval-shipment",
        idempotency_key="shipment-event-1",
    )
    assert shipment.approval_id == "approval-shipment"

    nonconformity = NonconformityRequest(
        nonconformity=Nonconformity(
            nonconformity_id="nc-1",
            description="Damaged package",
            severity="major",
            created_at=datetime.now(UTC),
        ),
        idempotency_key="nc-submit-1",
    )
    assert nonconformity.idempotency_key == "nc-submit-1"


def test_approval_contract_contains_hitl_operations() -> None:
    operation_schema = ApprovalRequest.model_json_schema()["properties"]["operation"]
    assert {
        "select_supplier",
        "approve_price",
        "send_rfq",
        "create_supplier_order",
        "record_shipment",
    }.issubset(set(operation_schema["enum"]))


def test_all_mutation_contracts_require_idempotency_key() -> None:
    mutation_models = (
        RFQDraftRequest,
        QuoteSubmission,
        RecommendationRequest,
        ApprovalRequest,
        ShipmentEventRequest,
        NonconformityRequest,
    )
    for model in mutation_models:
        assert "idempotency_key" in model.model_json_schema()["required"]


@pytest.mark.asyncio
async def test_normal_ui_posts_do_not_return_422(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubService:
        def __init__(self, db: object) -> None:
            _ = db

        async def search_suppliers(
            self,
            case_id: uuid.UUID,
            request: SupplierSearchRequest,
        ):
            _ = (case_id, request)
            return {
                "query": "inferred from case",
                "suppliers": [],
                "sources_used": [],
                "web_fallback_used": False,
            }

        async def recommendation(
            self,
            case_id: uuid.UUID,
            request: RecommendationRequest,
        ) -> RecommendationRecord:
            _ = case_id
            return RecommendationRecord(
                recommendation_id="recommendation-1",
                supplier_id=request.supplier_id,
                quote_id=request.quote_id,
                total=Decimal("1000"),
                currency="RUB",
                status="approval_required",
                created_at=datetime.now(UTC),
            )

    async def override_db():
        yield object()

    async def override_user():
        return SimpleNamespace(id=uuid.uuid4(), is_superuser=True, position=None)

    async def allow_access(db: object, user: object) -> None:
        _ = (db, user)

    async def no_commit(db: object) -> None:
        _ = db

    app.dependency_overrides[api_deps.get_db] = override_db
    app.dependency_overrides[api_deps.get_current_user] = override_user
    monkeypatch.setattr(endpoint_module, "ProcurementManagerService", StubService)
    monkeypatch.setattr(endpoint_module, "_require_access", allow_access)
    monkeypatch.setattr(endpoint_module, "_commit", no_commit)
    try:
        case_id = uuid.uuid4()
        search_response = await client.post(f"{PREFIX}/cases/{case_id}/supplier-search")
        assert search_response.status_code == 200
        assert search_response.json()["query"] == "inferred from case"

        recommendation_response = await client.post(
            f"{PREFIX}/cases/{case_id}/recommendation",
            json={
                "supplier_id": "supplier-1",
                "quote_id": "quote-1",
                "idempotency_key": "recommendation-ui-1",
            },
        )
        assert recommendation_response.status_code == 201
        assert recommendation_response.json()["status"] == "approval_required"
    finally:
        app.dependency_overrides.clear()
