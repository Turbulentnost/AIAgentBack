from __future__ import annotations

from decimal import Decimal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents.procurement_manager_agent.evaluate import (
    build_trusted_cost_estimate,
    evaluate_case_positions,
)
from app.agents.procurement_manager_agent.graph import build_graph
from app.agents.procurement_manager_agent.material_bank import reset_material_bank_for_tests
from app.agents.procurement_manager_agent.schemas import (
    Supplier,
    SupplierSearchRequest,
    SupplierSearchResult,
)
from app.agents.procurement_manager_agent.supplier_ranking import rank_supplier_offers


class Runtime:
    def __init__(self, internal_count: int, threshold: int = 2) -> None:
        self.internal_count = internal_count
        self.internal_threshold = threshold
        self.web_calls = 0

    async def search_internal(
        self,
        request: SupplierSearchRequest,
    ) -> SupplierSearchResult:
        return SupplierSearchResult(
            query=request.query or "",
            suppliers=[
                Supplier(supplier_id=f"internal-{index}", name=f"Internal {index}")
                for index in range(self.internal_count)
            ],
            sources_used=["internal"] if self.internal_count else [],
        )

    async def search_web(
        self,
        request: SupplierSearchRequest,
    ) -> SupplierSearchResult:
        self.web_calls += 1
        return SupplierSearchResult(
            query=request.query or "",
            suppliers=[Supplier(supplier_id="web-1", name="Web", source="web")],
            sources_used=["web"],
            web_fallback_used=True,
        )


POSITIONS = [
    {
        "line_id": "line-steel",
        "nomenclature_id": "steel",
        "nomenclature_name": "Сталь",
        "quantity": "10",
        "unit": "кг",
    }
]


async def _run(runtime: Runtime, *, positions: list[dict] | None = None) -> dict:
    return await build_graph().ainvoke(
        {
            "case_id": "case-1",
            "case_number": "REQ-1",
            "case_context": {"positions": positions or []},
            "positions": positions or [],
            "request": SupplierSearchRequest(query="steel").model_dump(mode="json"),
        },
        config={"configurable": {"runtime": runtime}},
    )


async def test_graph_skips_web_at_internal_threshold_and_interrupts() -> None:
    runtime = Runtime(internal_count=2)
    result = await _run(runtime)
    assert runtime.web_calls == 0
    assert result["web_fallback_used"] is False
    assert len(result["candidates"]) == 2
    assert result["__interrupt__"]
    interrupt = result["__interrupt__"][0]
    payload = getattr(interrupt, "value", interrupt)
    assert payload["type"] == "procurement_shortlist_approval"
    assert "approve_shortlist" in payload["allowed_actions"]


async def test_graph_routes_web_below_threshold_and_interrupts() -> None:
    runtime = Runtime(internal_count=1)
    result = await _run(runtime)
    assert runtime.web_calls == 1
    assert result["web_fallback_used"] is True
    assert {item["supplier_id"] for item in result["candidates"]} == {
        "internal-0",
        "web-1",
    }
    assert result["__interrupt__"]


async def test_graph_resume_reject_stops_before_po() -> None:
    runtime = Runtime(internal_count=2)
    checkpointer = MemorySaver()
    graph = build_graph(checkpointer=checkpointer)
    config = {
        "configurable": {
            "thread_id": "procurement-manager-test-reject",
            "runtime": runtime,
        }
    }
    paused = await graph.ainvoke(
        {
            "case_id": "case-1",
            "case_number": "REQ-1",
            "case_context": {},
            "positions": [],
            "request": SupplierSearchRequest(query="steel").model_dump(mode="json"),
        },
        config=config,
    )
    assert paused["__interrupt__"]
    resumed = await graph.ainvoke(
        Command(resume={"action": "reject"}),
        config=config,
    )
    assert resumed["status"] == "rejected"
    assert resumed.get("purchase_order_draft") is None


async def test_graph_search_rank_interrupt_resume_to_po_draft() -> None:
    reset_material_bank_for_tests()
    runtime = Runtime(internal_count=2)
    checkpointer = MemorySaver()
    graph = build_graph(checkpointer=checkpointer)
    config = {
        "configurable": {
            "thread_id": "procurement-manager-test-full",
            "runtime": runtime,
        }
    }
    paused = await graph.ainvoke(
        {
            "case_id": "case-1",
            "case_number": "REQ-1",
            "case_context": {"positions": POSITIONS},
            "positions": POSITIONS,
            "request": SupplierSearchRequest(query="steel").model_dump(mode="json"),
        },
        config=config,
    )
    assert paused["__interrupt__"]
    assert paused.get("evaluation")
    assert paused.get("rfq_draft")
    interrupt = paused["__interrupt__"][0]
    assert getattr(interrupt, "value", interrupt)["type"] == "procurement_shortlist_approval"

    after_shortlist = await graph.ainvoke(
        Command(resume={"action": "approve_shortlist"}),
        config=config,
    )
    assert after_shortlist["__interrupt__"]
    assert after_shortlist.get("cost_estimate")
    assert after_shortlist["cost_estimate"]["web_approved"] is True
    assert after_shortlist.get("purchase_order_draft")
    assert after_shortlist["purchase_order_draft"]["payment_execution_allowed"] is False
    assert after_shortlist["purchase_order_draft"]["status"] == "draft"
    order_interrupt = after_shortlist["__interrupt__"][0]
    assert (
        getattr(order_interrupt, "value", order_interrupt)["type"]
        == "procurement_order_approval"
    )

    finished = await graph.ainvoke(
        Command(resume={"action": "approve_order_draft"}),
        config=config,
    )
    assert finished["status"] == "order_draft_approved"
    assert finished.get("purchase_order_draft")
    assert finished["purchase_order_draft"]["payment_execution_allowed"] is False
    assert "execute_payment" not in (
        getattr(order_interrupt, "value", order_interrupt).get("allowed_actions") or []
    )


async def test_ranking_consistent_with_all_positions_top_suppliers() -> None:
    bank = reset_material_bank_for_tests()
    need = Decimal("10")
    direct = rank_supplier_offers("steel", need, bank=bank, top_n=3)
    # Same optimizer without bank remainder adjustment (full need).
    evaluation = evaluate_case_positions(
        POSITIONS, bank=bank, top_n=3, use_bank_first=False
    )
    line = evaluation["lines"][0]
    assert [row["supplier_id"] for row in line["top_suppliers"]] == [
        row["supplier_id"] for row in direct
    ]
    assert [row["score"] for row in line["top_suppliers"]] == [row["score"] for row in direct]


def test_agent_estimate_excludes_unapproved_web_suppliers() -> None:
    reset_material_bank_for_tests()
    web_candidate = {
        "supplier_id": "web-unapproved",
        "name": "Web Unapproved",
        "source": "web",
        "url": "https://example.com/web",
        "unit_price": "1.00",
    }
    trusted = {
        "supplier_id": "internal-1",
        "name": "Trusted Internal",
        "source": "internal",
        "evidence": ["bank:internal-1"],
    }
    before = build_trusted_cost_estimate(
        POSITIONS,
        candidates=[trusted, web_candidate],
        web_candidates=[web_candidate],
        web_approved=False,
    )
    assert before["web_approved"] is False
    assert before["excluded_unapproved_web"] is True
    assert before["approved_web_supplier_ids"] == []
    for line in before["lines"]:
        assert all(
            str(offer.get("supplier_id")) != "web-unapproved"
            for offer in (line.get("top_suppliers") or [])
        )
        assert all(
            str(offer.get("source") or "").casefold() != "web"
            for offer in (line.get("top_suppliers") or [])
        )

    after = build_trusted_cost_estimate(
        POSITIONS,
        candidates=[trusted, web_candidate],
        web_candidates=[web_candidate],
        web_approved=True,
    )
    assert after["web_approved"] is True
    assert "web-unapproved" in after["approved_web_supplier_ids"]
    assert any(
        str(offer.get("supplier_id")) == "web-unapproved"
        for line in after["lines"]
        for offer in (line.get("top_suppliers") or [])
    )
