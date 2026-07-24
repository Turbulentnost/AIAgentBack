"""Queue-level LangGraph: urgency waves → bank → search → optimize → HITL → multi-PO."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol, TypedDict
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from app.agents.procurement_manager_agent.documents import render_purchase_order_draft
from app.agents.procurement_manager_agent.evaluate import build_trusted_cost_estimate
from app.agents.procurement_manager_agent.optimize import optimize_queue_coverage
from app.agents.procurement_manager_agent.schemas import (
    NomenclatureSearchItem,
    PurchaseOrderLine,
    Supplier,
    SupplierSearchRequest,
)
from app.agents.procurement_manager_agent.strategy_agent import (
    explain_tradeoffs,
    plan_waves,
    propose_supplier_policy,
)
from app.agents.procurement_manager_agent.suppliers import (
    MIN_SUPPLIERS_BEFORE_SKIP,
    WEB_LIMIT_PER_NOMENCLATURE,
    HybridSupplierSearchService,
    qualifying_suppliers_for_skip,
)
from app.agents.procurement_manager_agent.waves import allocate_queue_with_waves


class StrategyGraphRuntime(Protocol):
    internal_threshold: int

    async def search_internal(self, request: SupplierSearchRequest) -> Any: ...

    async def search_web(self, request: SupplierSearchRequest) -> Any: ...


class StrategyGraphState(TypedDict, total=False):
    manager_id: str
    cases: list[dict[str, Any]]
    case_ids: list[str]
    request: dict[str, Any]
    today: str
    waves: dict[str, Any] | None
    wave_plan: dict[str, Any] | None
    allocation: dict[str, Any] | None
    queue_plan: dict[str, Any] | None
    internal_candidates: list[dict[str, Any]]
    web_candidates: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    nomenclature_results: list[dict[str, Any]]
    sources_used: list[str]
    web_fallback_used: bool
    supply_policy: dict[str, Any] | None
    explanation: dict[str, Any] | None
    cost_estimate: dict[str, Any] | None
    purchase_order_drafts: list[dict[str, Any]]
    policy_approval: dict[str, Any] | None
    order_approval: dict[str, Any] | None
    kpi_flags: dict[str, Any]
    status: str
    stage: str


def _runtime(config: RunnableConfig) -> StrategyGraphRuntime:
    runtime = (config.get("configurable") or {}).get("runtime")
    if runtime is None:
        raise RuntimeError("configurable.runtime is required for strategy graph")
    return runtime


def _today(state: StrategyGraphState) -> date:
    raw = state.get("today")
    if raw:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            pass
    return date.today()


def _nom_key(row: dict[str, Any]) -> str:
    return str(
        row.get("nomenclature_id")
        or row.get("nomenclature_name")
        or row.get("query")
        or ""
    ).strip().casefold()


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


def load_queue(state: StrategyGraphState) -> dict[str, Any]:
    cases = list(state.get("cases") or [])
    case_ids = [
        str(item.get("id") or item.get("case_id") or "")
        for item in cases
        if isinstance(item, dict)
    ]
    case_ids = [cid for cid in case_ids if cid]
    return {
        "cases": cases,
        "case_ids": case_ids,
        "stage": "load_queue",
        "status": "queue_loaded",
        "kpi_flags": {
            "comparable_quotes_ok": False,
            "comparison_complete": False,
            "supplier_confirmed": False,
            "payment_execution_allowed": False,
        },
        "purchase_order_drafts": [],
    }


async def plan_urgency_waves(state: StrategyGraphState) -> dict[str, Any]:
    cases = list(state.get("cases") or [])
    plan = await plan_waves(cases, today=_today(state))
    # Prefer structured raw_waves for optimizer case_wave index.
    wave_payload = plan.get("raw_waves") if isinstance(plan.get("raw_waves"), dict) else None
    if not wave_payload:
        wave_payload = {
            "waves": plan.get("waves") or [],
            "case_wave": plan.get("case_wave") or {},
            "summary": plan.get("summary") or {},
            "today": plan.get("today"),
            "source": plan.get("source"),
        }
    else:
        wave_payload = {
            **wave_payload,
            "rationale": plan.get("rationale"),
            "source": plan.get("source"),
        }
    return {
        "waves": wave_payload,
        "wave_plan": plan,
        "stage": "plan_urgency_waves",
        "status": "waves_planned",
    }


def allocate_bank_global(state: StrategyGraphState) -> dict[str, Any]:
    cases = list(state.get("cases") or [])
    try:
        allocation = allocate_queue_with_waves(cases, today=_today(state))
    except Exception:
        allocation = {"error": "allocation_skipped", "cases": [], "lines": [], "waves": {}}
    # Overlay Qwen/deterministic wave ids onto allocation when present.
    waves = state.get("waves") or {}
    case_wave = dict(waves.get("case_wave") or {})
    if case_wave:
        wave_meta = {
            str(w.get("wave_id")): w
            for w in (waves.get("waves") or [])
            if isinstance(w, dict)
        }
        for case_row in allocation.get("cases") or []:
            if not isinstance(case_row, dict):
                continue
            cid = str(case_row.get("case_id") or "")
            wid = case_wave.get(cid)
            if not wid:
                continue
            case_row["wave_id"] = wid
            meta = wave_meta.get(wid) or {}
            case_row["wave_label"] = meta.get("label")
            case_row["wave_mode"] = meta.get("mode")
        for line in allocation.get("lines") or []:
            if not isinstance(line, dict):
                continue
            cid = str(line.get("case_id") or "")
            wid = case_wave.get(cid)
            if not wid:
                continue
            line["wave_id"] = wid
            meta = wave_meta.get(wid) or {}
            line["wave_label"] = meta.get("label")
            line["wave_mode"] = meta.get("mode")
        allocation["waves"] = waves
    return {
        "allocation": allocation,
        "stage": "allocate_bank_global",
        "status": "allocated",
    }


def _uncovered_nomenclatures(state: StrategyGraphState) -> list[NomenclatureSearchItem]:
    allocation = state.get("allocation") or {}
    seen: set[str] = set()
    items: list[NomenclatureSearchItem] = []
    for line in allocation.get("lines") or []:
        if not isinstance(line, dict):
            continue
        try:
            deficit = Decimal(str(line.get("deficit_quantity") or 0))
            from_supplier = Decimal(str(line.get("from_supplier") or 0))
            needed = Decimal(str(line.get("needed_quantity") or 0))
            from_wh = Decimal(str(line.get("from_warehouse") or 0))
        except Exception:
            continue
        remainder = needed - from_wh
        if remainder <= 0 and deficit <= 0 and from_supplier <= 0:
            continue
        # Search when warehouse alone does not cover, or there is deficit.
        if from_wh + Decimal("0.000001") >= needed and deficit <= 0:
            continue
        nom_id = str(line.get("nomenclature_id") or "").strip()
        nom_name = str(line.get("nomenclature_name") or nom_id or "").strip()
        query = nom_name or nom_id
        if len(query) < 2:
            continue
        key = (nom_id or nom_name).casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(
            NomenclatureSearchItem(
                nomenclature_id=nom_id or None,
                nomenclature_name=nom_name or query,
                query=query[:500],
            )
        )
    if items:
        return items
    # Fallback: all positions from queue cases.
    for case in state.get("cases") or []:
        if not isinstance(case, dict):
            continue
        for position in case.get("positions") or []:
            if not isinstance(position, dict) or position.get("cancelled"):
                continue
            nom_id = str(position.get("nomenclature_id") or "").strip()
            nom_name = str(position.get("nomenclature_name") or nom_id or "").strip()
            query = nom_name or nom_id
            if len(query) < 2:
                continue
            key = (nom_id or nom_name).casefold()
            if key in seen:
                continue
            seen.add(key)
            items.append(
                NomenclatureSearchItem(
                    nomenclature_id=nom_id or None,
                    nomenclature_name=nom_name or query,
                    query=query[:500],
                )
            )
    return items


async def gather_internal(
    state: StrategyGraphState,
    config: RunnableConfig,
) -> dict[str, Any]:
    nomenclatures = _uncovered_nomenclatures(state)
    base = dict(state.get("request") or {})
    query = base.get("query") or ", ".join(
        (item.query or item.nomenclature_name or "") for item in nomenclatures[:5]
    )
    request = SupplierSearchRequest(
        query=(str(query)[:500] if query else "поставщик"),
        allow_web_fallback=bool(base.get("allow_web_fallback", True)),
        limit=int(base.get("limit") or 10),
        nomenclatures=nomenclatures,
        idempotency_key=base.get("idempotency_key"),
    )
    result = await _runtime(config).search_internal(request)
    return {
        "internal_candidates": [
            item.model_dump(mode="json") for item in result.suppliers
        ],
        "nomenclature_results": [
            item.model_dump(mode="json") for item in result.nomenclature_results
        ],
        "sources_used": list(result.sources_used or []),
        "request": request.model_dump(mode="json"),
        "stage": "gather_internal",
        "status": "internal_gathered",
    }


def decide_web(state: StrategyGraphState, config: RunnableConfig) -> dict[str, Any]:
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
    request = SupplierSearchRequest.model_validate(state.get("request") or {"query": "x" * 2})
    force_web = request.is_manual_web
    needs_web = False
    for row in state.get("nomenclature_results") or []:
        if not isinstance(row, dict):
            continue
        qualifying = qualifying_suppliers_for_skip(
            _row_suppliers_as_models(list(row.get("suppliers") or [])),
            force_web=force_web,
        )
        if len(qualifying) >= MIN_SUPPLIERS_BEFORE_SKIP:
            continue
        if len(qualifying) < threshold:
            needs_web = True
            break
    return {
        "web_fallback_used": bool(request.allow_web_fallback and needs_web),
        "status": "web_required" if needs_web else "internal_sufficient",
        "stage": "decide_web",
    }


def _after_decide_web(state: StrategyGraphState) -> str:
    return "gather_web" if state.get("web_fallback_used") else "optimize_wave_loop"


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


async def gather_web(
    state: StrategyGraphState,
    config: RunnableConfig,
) -> dict[str, Any]:
    request = SupplierSearchRequest.model_validate(state.get("request") or {"query": "x" * 2})
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
        need_web.append(
            NomenclatureSearchItem(
                nomenclature_id=row.get("nomenclature_id"),
                nomenclature_name=row.get("nomenclature_name"),
                query=query,
            )
        )
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
        "stage": "gather_web",
        "status": "web_gathered",
    }


def _offers_from_nomenclature_results(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    offers_by_nom: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        nom = str(row.get("nomenclature_id") or "").strip()
        keys = [k for k in (nom, nom.casefold(), _nom_key(row)) if k]
        for supplier in row.get("suppliers") or []:
            if not isinstance(supplier, dict):
                continue
            price = supplier.get("unit_price")
            if price is None:
                price = supplier.get("approx_cost")
            if price is None:
                continue
            try:
                unit_price = Decimal(str(price))
            except Exception:
                continue
            offer = {
                "supplier_id": str(supplier.get("supplier_id") or ""),
                "supplier_name": str(supplier.get("name") or supplier.get("supplier_id") or ""),
                "nomenclature_id": nom or None,
                "nomenclature_name": row.get("nomenclature_name"),
                "unit_price": unit_price,
                "available_qty": Decimal(
                    str(supplier.get("available_qty") or supplier.get("coverable_qty") or "999999")
                ),
                "lead_time_days": supplier.get("lead_time_days")
                or supplier.get("delivery_days"),
                "source": supplier.get("source") or "internal",
            }
            for key in keys:
                bucket = offers_by_nom.setdefault(key, [])
                if not any(item.get("supplier_id") == offer["supplier_id"] for item in bucket):
                    bucket.append(offer)
    return offers_by_nom


def optimize_wave_loop(state: StrategyGraphState) -> dict[str, Any]:
    cases = list(state.get("cases") or [])
    waves = state.get("waves")
    offers = _offers_from_nomenclature_results(state.get("nomenclature_results") or [])
    plan = optimize_queue_coverage(
        cases,
        offers_by_nom=offers or None,
        today=_today(state),
        waves=waves,
    )
    # Flatten candidates from nomenclature results.
    flat: dict[str, dict[str, Any]] = {}
    for row in state.get("nomenclature_results") or []:
        if not isinstance(row, dict):
            continue
        for item in row.get("suppliers") or []:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("tax_id") or item.get("supplier_id") or "")
            if sid:
                flat.setdefault(sid, item)
    for item in state.get("internal_candidates") or []:
        if isinstance(item, dict) and item.get("supplier_id"):
            flat.setdefault(str(item.get("tax_id") or item["supplier_id"]), item)
    for item in state.get("web_candidates") or []:
        if isinstance(item, dict) and item.get("supplier_id"):
            flat.setdefault(str(item.get("tax_id") or item["supplier_id"]), item)
    return {
        "queue_plan": plan,
        "allocation": plan.get("allocation") or state.get("allocation"),
        "waves": plan.get("waves") or waves,
        "candidates": list(flat.values()),
        "stage": "optimize_wave_loop",
        "status": "optimized",
    }


async def compose_policy(state: StrategyGraphState) -> dict[str, Any]:
    queue_plan = state.get("queue_plan") or {}
    policy = await propose_supplier_policy(
        queue_plan,
        allocation=state.get("allocation"),
        web_candidates=list(state.get("web_candidates") or []),
    )
    explanation = await explain_tradeoffs(
        policy,
        waves=state.get("waves"),
        queue_plan=queue_plan,
    )
    supply_policy = {
        "waves": state.get("waves"),
        "wave_plan": state.get("wave_plan"),
        "policy": policy,
        "explanation": explanation,
        "assignments": policy.get("assignments"),
        "shortlist_supplier_ids": policy.get("shortlist_supplier_ids"),
        "supplier_diversity": policy.get("supplier_diversity")
        or queue_plan.get("supplier_diversity"),
        "queue_summary": queue_plan.get("summary"),
        "generated_at": datetime.now(UTC).isoformat(),
        "payment_execution_allowed": False,
    }
    return {
        "supply_policy": supply_policy,
        "explanation": explanation,
        "status": "policy_approval_required",
        "stage": "compose_policy",
    }


def await_policy_hitl(state: StrategyGraphState) -> dict[str, Any]:
    decision = interrupt(
        {
            "type": "procurement_policy_approval",
            "case_ids": state.get("case_ids"),
            "supply_policy": state.get("supply_policy"),
            "explanation": state.get("explanation"),
            "queue_plan": {
                "summary": (state.get("queue_plan") or {}).get("summary"),
                "supplier_diversity": (state.get("queue_plan") or {}).get(
                    "supplier_diversity"
                ),
            },
            "web_candidates": state.get("web_candidates"),
            "allowed_actions": ["approve_shortlist", "approve_policy", "reject"],
            "forbidden_actions": [
                "send_rfq",
                "create_order",
                "execute_payment",
                "post_to_1c",
            ],
        }
    )
    approved = isinstance(decision, dict) and decision.get("action") in {
        "approve_shortlist",
        "approve_policy",
        "approve_rfq_draft",
    }
    flags = dict(state.get("kpi_flags") or {})
    if approved:
        flags["supplier_confirmed"] = True
    return {
        "policy_approval": dict(decision) if isinstance(decision, dict) else {"action": "reject"},
        "kpi_flags": flags,
        "status": "policy_approved" if approved else "rejected",
        "stage": "await_policy_hitl",
    }


def _after_policy(state: StrategyGraphState) -> str:
    return "compose_estimates_and_pos" if state.get("status") == "policy_approved" else END


def _positions_from_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id") or case.get("case_id") or "")
        case_required = case.get("required_date")
        for position in case.get("positions") or []:
            if not isinstance(position, dict) or position.get("cancelled"):
                continue
            positions.append(
                {
                    **position,
                    "case_id": case_id,
                    "line_id": str(
                        position.get("line_id") or position.get("id") or uuid4()
                    ),
                    "required_date": position.get("required_date") or case_required,
                }
            )
    return positions


def compose_estimates_and_pos(state: StrategyGraphState) -> dict[str, Any]:
    """Смета from trusted + approved web; PO drafts grouped by supplier (multi-PO)."""
    approved = state.get("status") == "policy_approved"
    positions = _positions_from_cases(list(state.get("cases") or []))
    estimate = build_trusted_cost_estimate(
        positions,
        candidates=list(state.get("candidates") or []),
        web_candidates=list(state.get("web_candidates") or []),
        web_approved=approved,
    )
    # Prefer policy assignments for PO grouping.
    policy = (state.get("supply_policy") or {}).get("policy") or {}
    queue_plan = state.get("queue_plan") or {}
    line_by_key = {
        f"{line.get('case_id')}:{line.get('line_id')}": line
        for line in (queue_plan.get("lines") or [])
        if isinstance(line, dict)
    }
    by_supplier: dict[str, list[PurchaseOrderLine]] = {}
    supplier_names: dict[str, str] = {}
    for assignment in policy.get("assignments") or []:
        if not isinstance(assignment, dict):
            continue
        sid = str(assignment.get("supplier_id") or "").strip()
        if not sid:
            continue
        key = f"{assignment.get('case_id')}:{assignment.get('line_id')}"
        line = line_by_key.get(key) or {}
        try:
            qty = Decimal(
                str(
                    (assignment.get("supplier_parts") or [{}])[0].get("quantity")
                    if assignment.get("supplier_parts")
                    else line.get("supplier_remainder") or line.get("needed_quantity") or 0
                )
            )
        except Exception:
            qty = Decimal("0")
        if qty <= 0:
            continue
        unit_price = Decimal("0")
        parts = assignment.get("supplier_parts") or []
        if parts and isinstance(parts[0], dict) and parts[0].get("unit_price") is not None:
            try:
                unit_price = Decimal(str(parts[0]["unit_price"]))
            except Exception:
                unit_price = Decimal("0")
        if unit_price <= 0:
            for offer in line.get("top_suppliers") or []:
                if str(offer.get("supplier_id")) == sid:
                    try:
                        unit_price = Decimal(str(offer.get("unit_price") or 0))
                    except Exception:
                        unit_price = Decimal("0")
                    break
        lead = None
        for offer in line.get("top_suppliers") or []:
            if str(offer.get("supplier_id")) == sid:
                lead = offer.get("lead_time_days")
                break
        po_line = PurchaseOrderLine(
            line_id=str(assignment.get("line_id") or key),
            nomenclature_id=str(line.get("nomenclature_id") or assignment.get("line_id") or ""),
            description=str(
                line.get("nomenclature_name")
                or line.get("nomenclature_id")
                or assignment.get("line_id")
                or "Позиция"
            ),
            quantity=qty,
            unit="шт",
            unit_price=unit_price,
            delivery_days=int(lead or 7),
        )
        by_supplier.setdefault(sid, []).append(po_line)
        supplier_names[sid] = str(
            assignment.get("supplier_name") or supplier_names.get(sid) or sid
        )

    drafts: list[dict[str, Any]] = []
    case_label = ",".join((state.get("case_ids") or [])[:3]) or "queue"
    for sid, lines in by_supplier.items():
        if not lines:
            continue
        draft = render_purchase_order_draft(
            supplier_id=sid,
            supplier_name=supplier_names.get(sid, sid),
            lines=lines,
            case_number=f"strategy:{case_label}",
        )
        payload = draft.model_dump(mode="json")
        payload["payment_execution_allowed"] = False
        payload["executed"] = False
        payload["status"] = "draft"
        drafts.append(payload)

    supply_policy = dict(state.get("supply_policy") or {})
    supply_policy["cost_estimate"] = estimate
    supply_policy["purchase_order_drafts"] = drafts

    flags = dict(state.get("kpi_flags") or {})
    flags.update(dict(estimate.get("kpi_flags") or {}))
    return {
        "cost_estimate": estimate,
        "purchase_order_drafts": drafts,
        "supply_policy": supply_policy,
        "kpi_flags": flags,
        "status": "order_approval_required",
        "stage": "compose_estimates_and_pos",
    }


def await_order_hitl(state: StrategyGraphState) -> dict[str, Any]:
    decision = interrupt(
        {
            "type": "procurement_order_approval",
            "case_ids": state.get("case_ids"),
            "purchase_order_drafts": state.get("purchase_order_drafts"),
            "cost_estimate": state.get("cost_estimate"),
            "supply_policy": state.get("supply_policy"),
            "allowed_actions": ["approve_order_draft", "reject"],
            "forbidden_actions": ["send_order", "execute_payment", "post_to_1c"],
        }
    )
    approved = isinstance(decision, dict) and decision.get("action") == "approve_order_draft"
    drafts = list(state.get("purchase_order_drafts") or [])
    if approved:
        for draft in drafts:
            if isinstance(draft, dict):
                draft["status"] = "approved_draft"
                draft["payment_execution_allowed"] = False
                draft["executed"] = False
    return {
        "order_approval": dict(decision) if isinstance(decision, dict) else {"action": "reject"},
        "purchase_order_drafts": drafts,
        "status": "order_draft_approved" if approved else "order_rejected",
        "stage": "await_order_hitl",
    }


def persist_strategy(state: StrategyGraphState) -> dict[str, Any]:
    supply_policy = dict(state.get("supply_policy") or {})
    supply_policy["final_status"] = state.get("status")
    supply_policy["purchase_order_drafts"] = state.get("purchase_order_drafts") or []
    supply_policy["persisted_at"] = datetime.now(UTC).isoformat()
    supply_policy["payment_execution_allowed"] = False
    return {
        "supply_policy": supply_policy,
        "status": state.get("status") or "completed",
        "stage": "persist",
    }


def build_strategy_graph(*, checkpointer: Any | None = None):
    graph = StateGraph(StrategyGraphState)
    graph.add_node("load_queue", load_queue)
    graph.add_node("plan_urgency_waves", plan_urgency_waves)
    graph.add_node("allocate_bank_global", allocate_bank_global)
    graph.add_node("gather_internal", gather_internal)
    graph.add_node("decide_web", decide_web)
    graph.add_node("gather_web", gather_web)
    graph.add_node("optimize_wave_loop", optimize_wave_loop)
    graph.add_node("compose_policy", compose_policy)
    graph.add_node("await_policy_hitl", await_policy_hitl)
    graph.add_node("compose_estimates_and_pos", compose_estimates_and_pos)
    graph.add_node("await_order_hitl", await_order_hitl)
    graph.add_node("persist", persist_strategy)

    graph.set_entry_point("load_queue")
    graph.add_edge("load_queue", "plan_urgency_waves")
    graph.add_edge("plan_urgency_waves", "allocate_bank_global")
    graph.add_edge("allocate_bank_global", "gather_internal")
    graph.add_edge("gather_internal", "decide_web")
    graph.add_conditional_edges("decide_web", _after_decide_web)
    graph.add_edge("gather_web", "optimize_wave_loop")
    graph.add_edge("optimize_wave_loop", "compose_policy")
    graph.add_edge("compose_policy", "await_policy_hitl")
    graph.add_conditional_edges("await_policy_hitl", _after_policy)
    graph.add_edge("compose_estimates_and_pos", "await_order_hitl")
    graph.add_edge("await_order_hitl", "persist")
    graph.add_edge("persist", END)

    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


procurement_strategy_graph = build_strategy_graph()


def default_strategy_runtime() -> HybridSupplierSearchService:
    return HybridSupplierSearchService()


__all__ = [
    "StrategyGraphRuntime",
    "StrategyGraphState",
    "build_strategy_graph",
    "default_strategy_runtime",
    "procurement_strategy_graph",
]
