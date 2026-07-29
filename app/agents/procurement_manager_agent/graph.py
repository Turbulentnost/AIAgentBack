from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, TypedDict
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from app.agents.procurement_manager_agent.allocation import allocate_materials_by_deadline
from app.agents.procurement_manager_agent.documents import (
    render_purchase_order_draft,
    render_rfq_draft,
)
from app.agents.procurement_manager_agent.evaluate import (
    build_trusted_cost_estimate,
    evaluate_case_positions,
    evaluate_quotes,
)
from app.agents.procurement_manager_agent.schemas import (
    NomenclatureSearchItem,
    NomenclatureSupplierResult,
    PurchaseOrderLine,
    QuoteLine,
    RFQDraftRequest,
    RFQLine,
    Supplier,
    SupplierQuote,
    SupplierSearchRequest,
    SupplierSearchResult,
)
from app.agents.procurement_manager_agent.suppliers import (
    MIN_SUPPLIERS_BEFORE_SKIP,
    WEB_LIMIT_PER_NOMENCLATURE,
    HybridSupplierSearchService,
    qualifying_suppliers_for_skip,
)


class ProcurementManagerGraphRuntime(Protocol):
    internal_threshold: int

    async def search_internal(self, request: SupplierSearchRequest) -> SupplierSearchResult: ...

    async def search_web(self, request: SupplierSearchRequest) -> SupplierSearchResult: ...


class ProcurementManagerGraphState(TypedDict, total=False):
    case_id: str
    case_number: str
    case_context: dict[str, Any]
    request: dict[str, Any]
    positions: list[dict[str, Any]]
    allocation: dict[str, Any] | None
    internal_candidates: list[dict[str, Any]]
    web_candidates: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    nomenclature_results: list[dict[str, Any]]
    sources_used: list[str]
    web_fallback_used: bool
    evaluation: dict[str, Any] | None
    cost_estimate: dict[str, Any] | None
    recommendation: dict[str, Any] | None
    rfq_request: dict[str, Any] | None
    rfq_draft: dict[str, Any] | None
    shortlist_approval: dict[str, Any] | None
    quotes: list[dict[str, Any]]
    comparison: dict[str, Any] | None
    purchase_order_draft: dict[str, Any] | None
    order_approval: dict[str, Any] | None
    kpi_flags: dict[str, Any]
    status: str
    stage: str


def _runtime(config: RunnableConfig) -> ProcurementManagerGraphRuntime:
    runtime = (config.get("configurable") or {}).get("runtime")
    if runtime is None:
        raise RuntimeError("configurable.runtime is required for procurement manager graph")
    return runtime


def _positions(state: ProcurementManagerGraphState) -> list[dict[str, Any]]:
    if state.get("positions"):
        return list(state["positions"] or [])
    context = state.get("case_context") or {}
    return list(context.get("positions") or context.get("lines") or [])


async def load_context(state: ProcurementManagerGraphState) -> dict[str, Any]:
    positions = _positions(state)
    return {
        "case_context": dict(state.get("case_context") or {}),
        "positions": positions,
        "stage": "load_context",
        "status": "searching_internal",
        "kpi_flags": {
            "comparable_quotes_ok": False,
            "comparison_complete": False,
            "supplier_confirmed": False,
        },
    }


def allocate_bank(state: ProcurementManagerGraphState) -> dict[str, Any]:
    positions = _positions(state)
    case_stub = {
        "id": state.get("case_id"),
        "source_number": state.get("case_number"),
        "required_date": (state.get("case_context") or {}).get("required_date"),
        "positions": positions,
    }
    try:
        result = allocate_materials_by_deadline([case_stub])
        allocation = result if isinstance(result, dict) else getattr(result, "model_dump", lambda: {})()
        if hasattr(result, "model_dump"):
            allocation = result.model_dump(mode="json")
    except Exception:
        allocation = {"error": "allocation_skipped", "cases": []}
    return {
        "allocation": allocation,
        "stage": "allocate_bank",
        "status": "allocated",
    }


def _is_fixture_row(item: dict[str, Any]) -> bool:
    if str(item.get("supplier_id") or "").startswith("fixture-"):
        return True
    return any(
        evidence in {"internal_fixture"} or str(evidence).startswith("fixture:")
        for evidence in (item.get("evidence") or [])
    )


def _row_suppliers_as_models(rows: list[dict[str, Any]]) -> list[Supplier]:
    out: list[Supplier] = []
    for item in rows:
        if not isinstance(item, dict) or _is_fixture_row(item):
            continue
        try:
            out.append(Supplier.model_validate(item))
        except Exception:
            continue
    return out


def _nom_key(row: dict[str, Any]) -> str:
    return str(
        row.get("nomenclature_id")
        or row.get("nomenclature_name")
        or row.get("query")
        or ""
    ).strip().casefold()


async def search_internal(
    state: ProcurementManagerGraphState,
    config: RunnableConfig,
) -> dict[str, Any]:
    result = await _runtime(config).search_internal(
        SupplierSearchRequest.model_validate(state["request"])
    )
    return {
        "internal_candidates": [
            item.model_dump(mode="json") for item in result.suppliers
        ],
        "nomenclature_results": [
            item.model_dump(mode="json") for item in result.nomenclature_results
        ],
        "sources_used": result.sources_used,
        "stage": "search_internal",
    }


def decide_sufficiency(
    state: ProcurementManagerGraphState,
    config: RunnableConfig,
) -> dict[str, Any]:
    threshold = max(
        1,
        int(
            getattr(
                _runtime(config),
                "internal_threshold",
                os.environ.get("PROCUREMENT_MANAGER_INTERNAL_SUPPLIER_THRESHOLD", "1"),
            )
        ),
    )
    request = SupplierSearchRequest.model_validate(state["request"])
    force_web = request.is_manual_web
    nom_rows = state.get("nomenclature_results") or []
    if nom_rows:
        needs_web = False
        for row in nom_rows:
            qualifying = qualifying_suppliers_for_skip(
                _row_suppliers_as_models(list(row.get("suppliers") or [])),
                force_web=force_web,
            )
            # Bank seeds without links do not make web unnecessary.
            if len(qualifying) >= MIN_SUPPLIERS_BEFORE_SKIP:
                continue
            if len(qualifying) < threshold:
                needs_web = True
                break
        sufficient = not needs_web
    else:
        qualifying = qualifying_suppliers_for_skip(
            _row_suppliers_as_models(list(state.get("internal_candidates") or [])),
            force_web=force_web,
        )
        sufficient = len(qualifying) >= threshold
    return {
        "web_fallback_used": bool(request.allow_web_fallback and not sufficient),
        "status": "internal_sufficient" if sufficient else "web_required",
        "stage": "decide_sufficiency",
    }


def _after_sufficiency(state: ProcurementManagerGraphState) -> str:
    return "search_web" if state.get("web_fallback_used") else "normalize_dedupe"


async def search_web(
    state: ProcurementManagerGraphState,
    config: RunnableConfig,
) -> dict[str, Any]:
    request = SupplierSearchRequest.model_validate(state["request"])
    threshold = max(
        1,
        int(
            getattr(
                _runtime(config),
                "internal_threshold",
                os.environ.get("PROCUREMENT_MANAGER_INTERNAL_SUPPLIER_THRESHOLD", "1"),
            )
        ),
    )
    force_web = request.is_manual_web
    # Web only for nomenclatures still below threshold / under 3 qualifying suppliers.
    need_web: list[NomenclatureSearchItem] = []
    for row in state.get("nomenclature_results") or []:
        if not isinstance(row, dict):
            continue
        qualifying = qualifying_suppliers_for_skip(
            _row_suppliers_as_models(list(row.get("suppliers") or [])),
            force_web=force_web,
        )
        if len(qualifying) >= MIN_SUPPLIERS_BEFORE_SKIP:
            continue
        if len(qualifying) >= threshold:
            continue
        query = str(row.get("query") or row.get("nomenclature_name") or "").strip()
        if len(query) < 2:
            continue
        row_suppliers = _row_suppliers_as_models(list(row.get("suppliers") or []))
        need_web.append(
            NomenclatureSearchItem(
                nomenclature_id=row.get("nomenclature_id"),
                nomenclature_name=row.get("nomenclature_name"),
                query=query,
                # Carry prior cards with URLs so search_web can track them first.
                existing_suppliers=row_suppliers,
            )
        )
    if not need_web and request.nomenclatures:
        need_web = [
            item
            for item in request.nomenclatures
            if len(
                qualifying_suppliers_for_skip(
                    list(item.existing_suppliers or []),
                    force_web=force_web,
                )
            )
            < MIN_SUPPLIERS_BEFORE_SKIP
        ]
    web_request = request.model_copy(
        update={
            "nomenclatures": need_web,
            "limit": min(request.limit, WEB_LIMIT_PER_NOMENCLATURE),
        }
    )
    result = await _runtime(config).search_web(web_request)
    return {
        "web_candidates": [item.model_dump(mode="json") for item in result.suppliers],
        "nomenclature_results": _merge_web_into_nomenclature_results(
            state.get("nomenclature_results") or [],
            [item.model_dump(mode="json") for item in result.nomenclature_results],
        ),
        "sources_used": list(
            dict.fromkeys([*(state.get("sources_used") or []), *result.sources_used])
        ),
        "stage": "search_web",
    }


def _merge_web_into_nomenclature_results(
    internal_rows: list[dict[str, Any]],
    web_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key = {_nom_key(row): dict(row) for row in internal_rows if _nom_key(row)}
    for row in web_rows:
        key = _nom_key(row)
        if not key:
            continue
        current = by_key.get(key)
        if current is None:
            by_key[key] = dict(row)
            continue
        seen = {
            str(item.get("tax_id") or item.get("supplier_id"))
            for item in (current.get("suppliers") or [])
            if isinstance(item, dict)
        }
        merged = list(current.get("suppliers") or [])
        for item in row.get("suppliers") or []:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("tax_id") or item.get("supplier_id") or "")
            if sid and sid in seen:
                continue
            if sid:
                seen.add(sid)
            merged.append(item)
        sources = list(
            dict.fromkeys(
                [*(current.get("sources_used") or []), *(row.get("sources_used") or [])]
            )
        )
        by_key[key] = {
            **current,
            "suppliers": merged,
            "sources_used": sources,
            "web_fallback_used": True,
        }
    return list(by_key.values()) if by_key else list(web_rows)


def normalize_dedupe(state: ProcurementManagerGraphState) -> dict[str, Any]:
    request = SupplierSearchRequest.model_validate(state["request"])
    nom_rows = state.get("nomenclature_results") or []
    if nom_rows:
        normalized: list[dict[str, Any]] = []
        flat: list[Supplier] = []
        for row in nom_rows:
            unique: dict[str, Supplier] = {}
            for item in row.get("suppliers") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    supplier = Supplier.model_validate(item)
                except Exception:
                    continue
                unique.setdefault(supplier.tax_id or supplier.supplier_id, supplier)
            suppliers = list(unique.values())[: max(request.limit, WEB_LIMIT_PER_NOMENCLATURE)]
            flat.extend(suppliers)
            normalized.append(
                {
                    **row,
                    "suppliers": [item.model_dump(mode="json") for item in suppliers],
                }
            )
        flat_unique: dict[str, Supplier] = {}
        for supplier in flat:
            flat_unique.setdefault(supplier.tax_id or supplier.supplier_id, supplier)
        return {
            "nomenclature_results": normalized,
            "candidates": [
                item.model_dump(mode="json") for item in flat_unique.values()
            ],
            "status": "candidates_normalized",
            "stage": "normalize_dedupe",
        }

    rows = [
        Supplier.model_validate(item)
        for item in [
            *(state.get("internal_candidates") or []),
            *(state.get("web_candidates") or []),
        ]
    ]
    unique: dict[str, Supplier] = {}
    for supplier in rows:
        unique.setdefault(supplier.tax_id or supplier.supplier_id, supplier)
    candidates = list(unique.values())[: request.limit]
    return {
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "nomenclature_results": [
            NomenclatureSupplierResult(
                nomenclature_id=None,
                nomenclature_name=request.query,
                query=request.query or "поставщик",
                suppliers=candidates,
                sources_used=list(state.get("sources_used") or []),
                web_fallback_used=bool(state.get("web_fallback_used")),
            ).model_dump(mode="json")
        ]
        if candidates
        else [],
        "status": "candidates_normalized",
        "stage": "normalize_dedupe",
    }


def rank_offers(state: ProcurementManagerGraphState) -> dict[str, Any]:
    case_required = (state.get("case_context") or {}).get("required_date")
    evaluation = evaluate_case_positions(
        _positions(state),
        top_n=3,
        case_required_date=case_required,
        use_bank_first=True,
    )
    primary = evaluation.get("primary_supplier_id")
    primary_name = None
    for line in evaluation.get("lines") or []:
        for offer in line.get("top_suppliers") or []:
            if offer.get("supplier_id") == primary:
                primary_name = offer.get("supplier_name")
                break
        if primary_name:
            break
    recommendation = (
        {
            "supplier_id": primary,
            "supplier_name": primary_name or primary,
            "score": None,
            "deterministic": True,
            "requires_human_approval": True,
            "evaluation_summary": {
                "lines_count": len(evaluation.get("lines") or []),
                "recommended_supplier_ids": evaluation.get("recommended_supplier_ids"),
            },
        }
        if primary
        else None
    )
    return {
        "evaluation": evaluation,
        "recommendation": recommendation,
        "kpi_flags": dict(evaluation.get("kpi_flags") or {}),
        "status": "ranked",
        "stage": "rank_offers",
    }


def compose_rfq(state: ProcurementManagerGraphState) -> dict[str, Any]:
    evaluation = state.get("evaluation") or {}
    supplier_ids = list(evaluation.get("recommended_supplier_ids") or [])
    if not supplier_ids and state.get("recommendation"):
        sid = (state.get("recommendation") or {}).get("supplier_id")
        if sid:
            supplier_ids = [str(sid)]
    if not supplier_ids:
        supplier_ids = [
            str(item.get("supplier_id"))
            for item in state.get("candidates") or []
            if item.get("supplier_id")
        ][:3]
    lines: list[RFQLine] = []
    for position in _positions(state):
        nom = str(
            position.get("nomenclature_id")
            or position.get("nomenclature_code")
            or ""
        ).strip()
        try:
            qty = Decimal(str(position.get("quantity") or position.get("qty") or 0))
        except Exception:
            qty = Decimal("0")
        if qty <= 0:
            continue
        lines.append(
            RFQLine(
                line_id=str(position.get("line_id") or position.get("id") or nom or uuid4()),
                nomenclature_id=nom or None,
                description=str(
                    position.get("nomenclature_name")
                    or position.get("name")
                    or nom
                    or "Позиция"
                ),
                quantity=qty,
                unit=str(position.get("unit") or "шт"),
                required_date=None,
            )
        )
    if not supplier_ids or not lines:
        return {
            "rfq_draft": None,
            "rfq_request": None,
            "status": "rfq_skipped",
            "stage": "compose_rfq",
        }
    request = RFQDraftRequest(
        supplier_ids=supplier_ids,
        lines=lines,
        terms=["Указать цену, срок и объём поставки"],
        idempotency_key=f"agent-rfq:{state.get('case_id')}",
    )
    suppliers = [
        Supplier.model_validate(item)
        for item in state.get("candidates") or []
        if item.get("supplier_id") in supplier_ids
    ]
    if not suppliers:
        suppliers = [
            Supplier(supplier_id=sid, name=sid) for sid in supplier_ids
        ]
    draft = render_rfq_draft(
        request,
        suppliers,
        case_number=state.get("case_number") or state.get("case_id") or "",
    )
    return {
        "rfq_request": request.model_dump(mode="json"),
        "rfq_draft": draft.model_dump(mode="json"),
        "status": "approval_required",
        "stage": "compose_rfq",
    }


def await_supplier_hitl(state: ProcurementManagerGraphState) -> dict[str, Any]:
    decision = interrupt(
        {
            "type": "procurement_shortlist_approval",
            "case_id": state.get("case_id"),
            "recommendation": state.get("recommendation"),
            "evaluation": state.get("evaluation"),
            "rfq_draft": state.get("rfq_draft"),
            "allowed_actions": ["approve_shortlist", "reject"],
            "forbidden_actions": ["send_rfq", "create_order", "execute_payment"],
        }
    )
    approved = isinstance(decision, dict) and decision.get("action") in {
        "approve_shortlist",
        "approve_rfq_draft",
    }
    flags = dict(state.get("kpi_flags") or {})
    if approved:
        flags["supplier_confirmed"] = True
    return {
        "shortlist_approval": dict(decision) if isinstance(decision, dict) else {"action": "reject"},
        "kpi_flags": flags,
        "status": "shortlist_approved" if approved else "rejected",
        "stage": "await_supplier_hitl",
    }


def _after_shortlist(state: ProcurementManagerGraphState) -> str:
    return "compose_cost_estimate" if state.get("status") == "shortlist_approved" else END


def compose_cost_estimate(state: ProcurementManagerGraphState) -> dict[str, Any]:
    """After HITL: смета from 1C/internal trusted + approved web only."""
    approved = state.get("status") == "shortlist_approved"
    case_required = (state.get("case_context") or {}).get("required_date")
    estimate = build_trusted_cost_estimate(
        _positions(state),
        candidates=list(state.get("candidates") or []),
        web_candidates=list(state.get("web_candidates") or []),
        web_approved=approved,
        case_required_date=case_required,
    )
    evaluation = dict(state.get("evaluation") or {})
    evaluation["cost_estimate"] = estimate
    evaluation["lines"] = estimate.get("lines") or evaluation.get("lines")
    evaluation["recommended_supplier_ids"] = estimate.get("recommended_supplier_ids")
    evaluation["primary_supplier_id"] = estimate.get("primary_supplier_id")
    flags = dict(state.get("kpi_flags") or {})
    flags.update(dict(estimate.get("kpi_flags") or {}))
    return {
        "cost_estimate": estimate,
        "evaluation": evaluation,
        "kpi_flags": flags,
        "stage": "compose_cost_estimate",
        "status": "estimate_ready" if approved else state.get("status"),
    }


def ingest_quotes(state: ProcurementManagerGraphState) -> dict[str, Any]:
    """Build synthetic quotes from ranked seed offers (or keep existing quotes)."""
    existing = list(state.get("quotes") or [])
    if existing:
        return {"quotes": existing, "stage": "ingest_quotes", "status": "quotes_ready"}
    evaluation = state.get("evaluation") or {}
    quotes: list[dict[str, Any]] = []
    for line in evaluation.get("lines") or []:
        for offer in (line.get("top_suppliers") or [])[:3]:
            quote_id = f"seed-{offer['supplier_id']}-{line['line_id']}"
            if any(item.get("quote_id") == quote_id for item in quotes):
                # merge line into existing quote
                for item in quotes:
                    if item["quote_id"] == quote_id:
                        item["lines"].append(
                            {
                                "line_id": line["line_id"],
                                "unit_price": str(offer["unit_price"]),
                                "quantity": str(offer["coverable_qty"]),
                                "delivery_days": int(offer.get("lead_time_days") or 7),
                                "compliant": True,
                            }
                        )
                        break
                continue
            quotes.append(
                {
                    "quote_id": quote_id,
                    "supplier_id": offer["supplier_id"],
                    "currency": "RUB",
                    "lines": [
                        {
                            "line_id": line["line_id"],
                            "unit_price": str(offer["unit_price"]),
                            "quantity": str(offer["coverable_qty"]),
                            "delivery_days": int(offer.get("lead_time_days") or 7),
                            "compliant": True,
                        }
                    ],
                    "quality_score": "70",
                    "risk_score": "20",
                    "received_at": datetime.now(UTC).isoformat(),
                }
            )
    return {
        "quotes": quotes,
        "stage": "ingest_quotes",
        "status": "quotes_ready",
    }


def compare_quote_nodes(state: ProcurementManagerGraphState) -> dict[str, Any]:
    quotes = [
        SupplierQuote.model_validate(item) for item in state.get("quotes") or []
    ]
    if not quotes:
        return {
            "comparison": None,
            "status": "comparison_empty",
            "stage": "compare_quotes",
        }
    comparison = evaluate_quotes(quotes)
    flags = dict(state.get("kpi_flags") or {})
    flags["comparison_complete"] = True
    flags["comparable_quotes_ok"] = len(quotes) >= 2
    return {
        "comparison": comparison.model_dump(mode="json"),
        "kpi_flags": flags,
        "status": "compared",
        "stage": "compare_quotes",
    }


def compose_purchase_order(state: ProcurementManagerGraphState) -> dict[str, Any]:
    evaluation = state.get("evaluation") or {}
    comparison = state.get("comparison") or {}
    recommended_quote_id = comparison.get("recommended_quote_id")
    supplier_id = (state.get("recommendation") or {}).get("supplier_id")
    unit_prices: dict[str, Decimal] = {}
    delivery_days = 7
    source_quote_id = recommended_quote_id
    for quote in state.get("quotes") or []:
        if recommended_quote_id and quote.get("quote_id") != recommended_quote_id:
            continue
        if not recommended_quote_id and supplier_id and quote.get("supplier_id") != supplier_id:
            continue
        supplier_id = quote.get("supplier_id") or supplier_id
        source_quote_id = quote.get("quote_id")
        for line in quote.get("lines") or []:
            unit_prices[str(line.get("line_id"))] = Decimal(str(line.get("unit_price") or 0))
            delivery_days = int(line.get("delivery_days") or delivery_days)
        if recommended_quote_id:
            break
    if not supplier_id:
        supplier_id = evaluation.get("primary_supplier_id")
    po_lines: list[PurchaseOrderLine] = []
    for line in evaluation.get("lines") or []:
        line_id = str(line.get("line_id"))
        need = Decimal(str(line.get("need_qty") or 0))
        top = (line.get("top_suppliers") or [{}])[0]
        price = unit_prices.get(line_id)
        if price is None and top:
            price = Decimal(str(top.get("unit_price") or 0))
            if not supplier_id:
                supplier_id = top.get("supplier_id")
        if price is None or need <= 0:
            continue
        coverable = Decimal(str(top.get("coverable_qty") or need)) if top else need
        qty = min(need, coverable) if coverable > 0 else need
        po_lines.append(
            PurchaseOrderLine(
                line_id=line_id,
                nomenclature_id=str(line.get("nomenclature_id") or ""),
                description=str(line.get("nomenclature_name") or line_id),
                quantity=qty,
                unit=str(line.get("unit") or "шт"),
                unit_price=price,
                delivery_days=delivery_days,
            )
        )
    if not supplier_id or not po_lines:
        return {
            "purchase_order_draft": None,
            "status": "po_skipped",
            "stage": "compose_purchase_order",
        }
    supplier_name = (state.get("recommendation") or {}).get("supplier_name") or supplier_id
    draft = render_purchase_order_draft(
        supplier_id=str(supplier_id),
        supplier_name=str(supplier_name),
        lines=po_lines,
        case_number=state.get("case_number") or state.get("case_id") or "",
        source_quote_id=str(source_quote_id) if source_quote_id else None,
    )
    return {
        "purchase_order_draft": draft.model_dump(mode="json"),
        "status": "order_approval_required",
        "stage": "compose_purchase_order",
    }


def await_order_hitl(state: ProcurementManagerGraphState) -> dict[str, Any]:
    decision = interrupt(
        {
            "type": "procurement_order_approval",
            "case_id": state.get("case_id"),
            "purchase_order_draft": state.get("purchase_order_draft"),
            "comparison": state.get("comparison"),
            "allowed_actions": ["approve_order_draft", "reject"],
            "forbidden_actions": ["send_order", "execute_payment", "post_to_1c"],
        }
    )
    approved = isinstance(decision, dict) and decision.get("action") == "approve_order_draft"
    return {
        "order_approval": dict(decision) if isinstance(decision, dict) else {"action": "reject"},
        "status": "order_draft_approved" if approved else "order_rejected",
        "stage": "await_order_hitl",
    }


def persist_artifacts(state: ProcurementManagerGraphState) -> dict[str, Any]:
    return {
        "status": state.get("status") or "completed",
        "stage": "persist_artifacts",
    }


def build_graph(*, checkpointer: Any | None = None):
    graph = StateGraph(ProcurementManagerGraphState)
    graph.add_node("load_context", load_context)
    graph.add_node("allocate_bank", allocate_bank)
    graph.add_node("search_internal", search_internal)
    graph.add_node("decide_sufficiency", decide_sufficiency)
    graph.add_node("search_web", search_web)
    graph.add_node("normalize_dedupe", normalize_dedupe)
    graph.add_node("rank_offers", rank_offers)
    graph.add_node("compose_rfq", compose_rfq)
    graph.add_node("await_supplier_hitl", await_supplier_hitl)
    graph.add_node("compose_cost_estimate", compose_cost_estimate)
    graph.add_node("ingest_quotes", ingest_quotes)
    graph.add_node("compare_quotes", compare_quote_nodes)
    graph.add_node("compose_purchase_order", compose_purchase_order)
    graph.add_node("await_order_hitl", await_order_hitl)
    graph.add_node("persist_artifacts", persist_artifacts)

    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "allocate_bank")
    graph.add_edge("allocate_bank", "search_internal")
    graph.add_edge("search_internal", "decide_sufficiency")
    graph.add_conditional_edges("decide_sufficiency", _after_sufficiency)
    graph.add_edge("search_web", "normalize_dedupe")
    graph.add_edge("normalize_dedupe", "rank_offers")
    graph.add_edge("rank_offers", "compose_rfq")
    graph.add_edge("compose_rfq", "await_supplier_hitl")
    graph.add_conditional_edges("await_supplier_hitl", _after_shortlist)
    graph.add_edge("compose_cost_estimate", "ingest_quotes")
    graph.add_edge("ingest_quotes", "compare_quotes")
    graph.add_edge("compare_quotes", "compose_purchase_order")
    graph.add_edge("compose_purchase_order", "await_order_hitl")
    graph.add_edge("await_order_hitl", "persist_artifacts")
    graph.add_edge("persist_artifacts", END)
    # LangGraph API / Studio rejects custom checkpointers on the exported graph.
    # Pass checkpointer only for in-app HITL (e.g. MemorySaver in the service).
    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


# Studio / langgraph.json entrypoint — no custom checkpointer.
procurement_manager_graph = build_graph()


def default_graph_runtime() -> HybridSupplierSearchService:
    return HybridSupplierSearchService()


__all__ = [
    "ProcurementManagerGraphRuntime",
    "ProcurementManagerGraphState",
    "build_graph",
    "default_graph_runtime",
    "procurement_manager_graph",
]
