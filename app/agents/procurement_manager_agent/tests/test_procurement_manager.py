from __future__ import annotations

from decimal import Decimal

import pytest

from app.agents.procurement_manager_agent.operations import MutationGate
from app.agents.procurement_manager_agent.schemas import (
    ApprovalRecord,
    QuoteLine,
    Supplier,
    SupplierQuote,
    SupplierSearchRequest,
)
from app.agents.procurement_manager_agent.scoring import compare_quotes
from app.agents.procurement_manager_agent.suppliers import (
    HybridSupplierSearchService,
    SupplierSearchAdapter,
)


def _quote(
    quote_id: str,
    *,
    price: str,
    days: int,
    quality: str,
    risk: str,
) -> SupplierQuote:
    return SupplierQuote(
        quote_id=quote_id,
        supplier_id=f"supplier-{quote_id}",
        lines=[
            QuoteLine(
                line_id="line-1",
                unit_price=Decimal(price),
                quantity=Decimal("10"),
                delivery_days=days,
            )
        ],
        quality_score=Decimal(quality),
        risk_score=Decimal(risk),
    )


def test_quote_scoring_is_deterministic() -> None:
    quotes = [
        _quote("cheap", price="10", days=10, quality="80", risk="10"),
        _quote("fast", price="12", days=2, quality="95", risk="5"),
    ]
    first = compare_quotes(quotes)
    second = compare_quotes(list(reversed(quotes)))
    assert first.recommended_quote_id == second.recommended_quote_id
    assert [(row.quote_id, row.final_score) for row in first.scores] == [
        (row.quote_id, row.final_score) for row in second.scores
    ]


def test_mutation_gate_requires_approved_id_and_forbids_payment() -> None:
    from datetime import UTC, datetime

    approval = ApprovalRecord(
        approval_id="approval-1",
        operation="create_supplier_order",
        status="approved",
        created_at=datetime.now(UTC),
    )
    assert (
        MutationGate.authorize("create_supplier_order", "approval-1", [approval]).approval_id
        == "approval-1"
    )
    with pytest.raises(PermissionError, match="approval_id"):
        MutationGate.authorize("create_supplier_order", None, [approval])
    with pytest.raises(PermissionError, match="Payment"):
        MutationGate.authorize("execute_payment", "approval-1", [approval])


class _Adapter(SupplierSearchAdapter):
    def __init__(self, rows: list[Supplier] | None = None, *, fail: bool = False) -> None:
        self.rows = rows or []
        self.fail = fail
        self.calls = 0

    async def search(self, query: str, category: str | None, limit: int) -> list[Supplier]:
        _ = (query, category, limit)
        self.calls += 1
        if self.fail:
            from app.agents.procurement_agent.mcp_client import MCPUnavailableError

            raise MCPUnavailableError("offline")
        return self.rows


@pytest.mark.asyncio
async def test_web_search_is_only_used_when_internal_sources_are_empty() -> None:
    onec = _Adapter(fail=True)
    internal = _Adapter([Supplier(supplier_id="internal-1", name="Internal")])
    web = _Adapter([Supplier(supplier_id="web-1", name="Web", source="web")])
    service = HybridSupplierSearchService(onec=onec, internal=internal, web=web)

    result = await service.search(
        SupplierSearchRequest(query="steel", idempotency_key="search-1")
    )

    assert [item.supplier_id for item in result.suppliers] == ["internal-1"]
    assert web.calls == 0
    assert result.web_fallback_used is False


@pytest.mark.asyncio
async def test_web_fallback_runs_after_empty_internal_search() -> None:
    web = _Adapter([Supplier(supplier_id="web-1", name="Web", source="web")])
    service = HybridSupplierSearchService(
        onec=_Adapter(fail=True),
        internal=_Adapter(),
        web=web,
    )
    result = await service.search(
        SupplierSearchRequest(query="rare item", idempotency_key="search-2")
    )
    assert web.calls == 1
    assert result.web_fallback_used is True
    assert result.sources_used == ["web"]
