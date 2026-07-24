"""End-to-end LangGraph chain for the AI procurement pipeline.

Models orchestrator-aligned stages:
  ingest need → source picker → coverage → supplier match → draft PO →
  HITL approval → manager handoff → quality handoff → finalize

Studio / ``langgraph.json`` export compiles **without** a custom checkpointer.
Pass ``checkpointer=`` to ``build_graph`` for in-app HITL resume.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.procurement_manager_agent.allocation import allocate_materials_by_deadline
from app.agents.procurement_manager_agent.documents import render_purchase_order_draft
from app.agents.procurement_manager_agent.evaluate import evaluate_case_positions
from app.agents.procurement_manager_agent.schemas import PurchaseOrderLine
from app.agents.procurement_pipeline.state import ProcurementPipelineState
from app.agents.procurement_role_agents.config import (
    OMTO_SUPPORT_MANAGER_AGENT_ID,
    OTK_HEAD_AGENT_ID,
    PROCUREMENT_LOGISTICS_AGENT_ID,
    PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
    WAREHOUSE_MANAGER_AGENT_ID,
    agent_id_for_source,
)
from app.models.enums import ProcurementCaseStatus, ProcurementSourceType

# Default when source_type is missing or unknown — engineer coverage path.
_DEFAULT_PICKER = PRODUCTION_PREPARATION_ENGINEER_AGENT_ID


def _positions(state: ProcurementPipelineState) -> list[dict[str, Any]]:
    if state.get("positions"):
        return list(state["positions"] or [])
    context = state.get("case_context") or {}
    return list(context.get("positions") or context.get("lines") or [])


def _normalize_position(raw: dict[str, Any], index: int) -> dict[str, Any]:
    line_id = str(raw.get("line_id") or raw.get("id") or f"line-{index + 1}")
    nom = str(
        raw.get("nomenclature_id")
        or raw.get("nomenclature_code")
        or raw.get("nomenclature")
        or ""
    ).strip()
    name = str(
        raw.get("nomenclature_name") or raw.get("name") or nom or f"Позиция {index + 1}"
    )
    qty = raw.get("quantity")
    if qty is None:
        qty = raw.get("qty")
    if qty is None and raw.get("gross_quantity") is not None:
        qty = raw.get("gross_quantity")
    return {
        "line_id": line_id,
        "nomenclature_id": nom or name,
        "nomenclature_name": name,
        "quantity": str(qty if qty is not None else "0"),
        "unit": str(raw.get("unit") or "шт"),
    }


def ingest_need(state: ProcurementPipelineState) -> dict[str, Any]:
    """Normalize inbound need / case context (KT data ingest)."""
    errors: list[str] = list(state.get("errors") or [])
    case_id = str(state.get("case_id") or uuid4())
    case_number = str(state.get("case_number") or case_id)
    source_type = str(
        state.get("source_type")
        or (state.get("case_context") or {}).get("source_type")
        or ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value
    )
    positions = [_normalize_position(item, i) for i, item in enumerate(_positions(state))]
    if not positions:
        errors.append("positions_empty")
        return {
            "case_id": case_id,
            "case_number": case_number,
            "correlation_id": str(state.get("correlation_id") or case_id),
            "source_type": source_type,
            "positions": [],
            "case_context": {
                **dict(state.get("case_context") or {}),
                "positions": [],
                "source_type": source_type,
            },
            "errors": errors,
            "stage": "ingest_need",
            "status": "failed",
            "case_status": ProcurementCaseStatus.BLOCKED.value,
            "stop_reason": "Нет позиций потребности для обработки.",
            "requires_human": False,
        }
    missing = [
        p["line_id"]
        for p in positions
        if not p.get("nomenclature_id") or Decimal(str(p.get("quantity") or 0)) <= 0
    ]
    if missing:
        errors.append("positions_incomplete")
        return {
            "case_id": case_id,
            "case_number": case_number,
            "correlation_id": str(state.get("correlation_id") or case_id),
            "source_type": source_type,
            "positions": positions,
            "case_context": {
                **dict(state.get("case_context") or {}),
                "positions": positions,
                "source_type": source_type,
            },
            "errors": errors,
            "stage": "ingest_need",
            "status": "human_required",
            "case_status": ProcurementCaseStatus.HUMAN_REQUIRED.value,
            "stop_reason": f"Неполные позиции: {', '.join(missing)}",
            "requires_human": True,
        }
    return {
        "case_id": case_id,
        "case_number": case_number,
        "correlation_id": str(state.get("correlation_id") or case_id),
        "source_type": source_type,
        "positions": positions,
        "case_context": {
            **dict(state.get("case_context") or {}),
            "positions": positions,
            "source_type": source_type,
        },
        "errors": errors,
        "stage": "ingest_need",
        "status": "ingested",
        "case_status": ProcurementCaseStatus.DATA_CHECK.value,
        "requires_human": False,
        "kpi_flags": {"coverage_checked": False, "supplier_matched": False, "po_drafted": False},
    }


def assign_picker(state: ProcurementPipelineState) -> dict[str, Any]:
    """Pick warehouse / engineer / initiator role from source type (orchestrator map)."""
    source_type = str(state.get("source_type") or "")
    try:
        picker = agent_id_for_source(source_type)
    except ValueError:
        picker = (
            WAREHOUSE_MANAGER_AGENT_ID
            if "transfer" in source_type
            else _DEFAULT_PICKER
        )
    return {
        "picker_agent": picker,
        "current_agent": picker,
        "next_agent": picker,
        "stage": "assign_picker",
        "status": "picker_assigned",
        "case_status": ProcurementCaseStatus.COVERAGE_CHECK.value,
    }


def check_coverage(state: ProcurementPipelineState) -> dict[str, Any]:
    """Warehouse / bank coverage check (shared material bank allocation)."""
    positions = _positions(state)
    case_stub = {
        "id": state.get("case_id"),
        "source_number": state.get("case_number"),
        "required_date": (state.get("case_context") or {}).get("required_date"),
        "positions": positions,
    }
    try:
        result = allocate_materials_by_deadline([case_stub])
        allocation = result if isinstance(result, dict) else {}
        if hasattr(result, "model_dump"):
            allocation = result.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 — keep pipeline runnable in Studio
        return {
            "allocation": {"error": str(exc)},
            "coverage_status": "failed",
            "deficit_positions": positions,
            "stage": "check_coverage",
            "status": "failed",
            "case_status": ProcurementCaseStatus.BLOCKED.value,
            "stop_reason": f"Ошибка расчёта обеспеченности: {type(exc).__name__}",
            "requires_human": False,
        }

    case_key = str(state.get("case_id") or "").strip().casefold()
    case_entry = (allocation.get("case_index") or {}).get(case_key) or {}
    lines: list[dict[str, Any]] = list(case_entry.get("lines") or [])
    if not lines:
        for item in allocation.get("cases") or []:
            lines.extend(list(item.get("lines") or []))
    if not lines and positions:
        lines = [
            {
                **pos,
                "needed_quantity": pos.get("quantity"),
                "deficit_quantity": pos.get("quantity"),
                "from_warehouse": "0",
                "coverage_source": "none",
            }
            for pos in positions
        ]

    # Purchase path when warehouse stock alone cannot cover the need.
    deficit_positions: list[dict[str, Any]] = []
    for line in lines:
        try:
            needed = Decimal(str(line.get("needed_quantity") or line.get("quantity") or 0))
            from_wh = Decimal(str(line.get("from_warehouse") or 0))
        except Exception:
            continue
        purchase_qty = needed - from_wh
        if purchase_qty > 0:
            deficit_positions.append(
                {
                    **line,
                    "deficit": str(purchase_qty),
                    "deficit_quantity": str(
                        line.get("deficit_quantity") or purchase_qty
                    ),
                    "purchase_qty": str(purchase_qty),
                }
            )

    flags = dict(state.get("kpi_flags") or {})
    flags["coverage_checked"] = True

    if not deficit_positions:
        return {
            "allocation": allocation,
            "coverage_status": "covered",
            "deficit_positions": [],
            "kpi_flags": flags,
            "stage": "check_coverage",
            "status": "covered",
            "case_status": ProcurementCaseStatus.CLOSED.value,
            "current_agent": state.get("picker_agent"),
            "next_agent": None,
            "requires_human": False,
            "summary": "Потребность закрыта со склада / без закупки.",
        }

    return {
        "allocation": allocation,
        "coverage_status": "deficit",
        "deficit_positions": deficit_positions,
        "kpi_flags": flags,
        "stage": "check_coverage",
        "status": "purchase_required",
        "case_status": ProcurementCaseStatus.PURCHASE_DRAFT.value,
        "current_agent": OMTO_SUPPORT_MANAGER_AGENT_ID,
        "next_agent": PROCUREMENT_LOGISTICS_AGENT_ID,
        "requires_human": False,
    }


def _after_ingest(state: ProcurementPipelineState) -> str:
    if state.get("status") == "failed":
        return "finalize"
    if state.get("requires_human") or state.get("status") == "human_required":
        return "require_human"
    return "assign_picker"


def _after_coverage(state: ProcurementPipelineState) -> str:
    coverage = state.get("coverage_status")
    if coverage == "failed" or state.get("status") == "failed":
        return "finalize"
    if coverage == "covered":
        return "finalize"
    if state.get("requires_human"):
        return "require_human"
    return "match_suppliers"


def match_suppliers(state: ProcurementPipelineState) -> dict[str, Any]:
    """Match / rank suppliers for deficit positions (manager evaluation logic)."""
    purchase_positions = list(state.get("deficit_positions") or _positions(state))
    # Map deficit qty onto quantity for ranking.
    normalized: list[dict[str, Any]] = []
    for item in purchase_positions:
        row = dict(item)
        qty = row.get("purchase_qty") or row.get("deficit") or row.get("quantity")
        row["quantity"] = str(qty or 0)
        if not row.get("nomenclature_id"):
            row["nomenclature_id"] = str(
                row.get("nomenclature_name") or row.get("line_id") or ""
            )
        normalized.append(row)

    evaluation = evaluate_case_positions(normalized, top_n=3)
    primary = evaluation.get("primary_supplier_id")
    primary_name = None
    for line in evaluation.get("lines") or []:
        for offer in line.get("top_suppliers") or []:
            if offer.get("supplier_id") == primary:
                primary_name = offer.get("supplier_name")
                break
        if primary_name:
            break

    if not primary:
        return {
            "evaluation": evaluation,
            "recommendation": None,
            "stage": "match_suppliers",
            "status": "human_required",
            "case_status": ProcurementCaseStatus.HUMAN_REQUIRED.value,
            "stop_reason": "Не найден поставщик для дефицитных позиций.",
            "requires_human": True,
            "current_agent": PROCUREMENT_LOGISTICS_AGENT_ID,
            "next_agent": PROCUREMENT_LOGISTICS_AGENT_ID,
        }

    flags = dict(state.get("kpi_flags") or {})
    flags["supplier_matched"] = True
    recommendation = {
        "supplier_id": primary,
        "supplier_name": primary_name or primary,
        "deterministic": True,
        "requires_human_approval": True,
        "recommended_supplier_ids": evaluation.get("recommended_supplier_ids"),
    }
    return {
        "evaluation": evaluation,
        "recommendation": recommendation,
        "kpi_flags": flags,
        "stage": "match_suppliers",
        "status": "suppliers_matched",
        "case_status": ProcurementCaseStatus.PURCHASE_DRAFT.value,
        "current_agent": PROCUREMENT_LOGISTICS_AGENT_ID,
        "next_agent": PROCUREMENT_LOGISTICS_AGENT_ID,
        "requires_human": False,
    }


def draft_po(state: ProcurementPipelineState) -> dict[str, Any]:
    """Compose purchase-order draft from ranked offers (no 1C post / no payment)."""
    evaluation = state.get("evaluation") or {}
    recommendation = state.get("recommendation") or {}
    supplier_id = recommendation.get("supplier_id") or evaluation.get("primary_supplier_id")
    supplier_name = recommendation.get("supplier_name") or supplier_id
    po_lines: list[PurchaseOrderLine] = []
    for line in evaluation.get("lines") or []:
        top = (line.get("top_suppliers") or [{}])[0]
        if not top:
            continue
        try:
            need = Decimal(str(line.get("need_qty") or 0))
            price = Decimal(str(top.get("unit_price") or 0))
            coverable = Decimal(str(top.get("coverable_qty") or need))
        except Exception:
            continue
        if need <= 0 or price < 0:
            continue
        qty = min(need, coverable) if coverable > 0 else need
        if not supplier_id:
            supplier_id = top.get("supplier_id")
            supplier_name = top.get("supplier_name") or supplier_id
        po_lines.append(
            PurchaseOrderLine(
                line_id=str(line.get("line_id")),
                nomenclature_id=str(line.get("nomenclature_id") or ""),
                description=str(line.get("nomenclature_name") or line.get("line_id")),
                quantity=qty,
                unit=str(line.get("unit") or "шт"),
                unit_price=price,
                delivery_days=int(top.get("lead_time_days") or 7),
            )
        )
    if not supplier_id or not po_lines:
        return {
            "purchase_order_draft": None,
            "stage": "draft_po",
            "status": "human_required",
            "case_status": ProcurementCaseStatus.HUMAN_REQUIRED.value,
            "stop_reason": "Не удалось сформировать черновик заказа.",
            "requires_human": True,
        }
    draft = render_purchase_order_draft(
        supplier_id=str(supplier_id),
        supplier_name=str(supplier_name or supplier_id),
        lines=po_lines,
        case_number=state.get("case_number") or state.get("case_id") or "",
    )
    flags = dict(state.get("kpi_flags") or {})
    flags["po_drafted"] = True
    return {
        "purchase_order_draft": draft.model_dump(mode="json"),
        "kpi_flags": flags,
        "stage": "draft_po",
        "status": "approval_required",
        "case_status": ProcurementCaseStatus.APPROVAL_REQUIRED.value,
        "requires_human": True,
    }


def await_approval(state: ProcurementPipelineState) -> dict[str, Any]:
    """HITL gate before any order send / payment (interrupt unless auto_approve)."""
    if state.get("auto_approve"):
        decision: dict[str, Any] = {
            "action": "approve_order_draft",
            "source": "auto_approve",
        }
    else:
        raw = interrupt(
            {
                "type": "procurement_pipeline_approval",
                "case_id": state.get("case_id"),
                "recommendation": state.get("recommendation"),
                "purchase_order_draft": state.get("purchase_order_draft"),
                "allowed_actions": ["approve_order_draft", "reject"],
                "forbidden_actions": [
                    "send_order",
                    "execute_payment",
                    "post_to_1c",
                ],
            }
        )
        decision = dict(raw) if isinstance(raw, dict) else {"action": "reject"}

    approved = decision.get("action") == "approve_order_draft"
    return {
        "approval": decision,
        "stage": "await_approval",
        "status": "order_approved" if approved else "rejected",
        "case_status": (
            ProcurementCaseStatus.ORDERED.value
            if approved
            else ProcurementCaseStatus.HUMAN_REQUIRED.value
        ),
        "requires_human": not approved,
        "stop_reason": None if approved else "Черновик заказа отклонён человеком.",
    }


def _after_match(state: ProcurementPipelineState) -> str:
    if state.get("requires_human") or state.get("status") == "human_required":
        return "require_human"
    return "draft_po"


def _after_draft(state: ProcurementPipelineState) -> str:
    if state.get("status") == "human_required" and not state.get("purchase_order_draft"):
        return "require_human"
    return "await_approval"


def _after_approval(state: ProcurementPipelineState) -> str:
    if state.get("status") == "order_approved":
        return "handoff_manager"
    return "require_human"


def handoff_manager(state: ProcurementPipelineState) -> dict[str, Any]:
    """Handoff to procurement logistics / OMTO manager workspace."""
    return {
        "current_agent": PROCUREMENT_LOGISTICS_AGENT_ID,
        "next_agent": OTK_HEAD_AGENT_ID,
        "stage": "handoff_manager",
        "status": "handed_to_manager",
        "case_status": ProcurementCaseStatus.ORDERED.value,
        "requires_human": False,
    }


def handoff_quality(state: ProcurementPipelineState) -> dict[str, Any]:
    """Handoff to quality / OTK queue after order path."""
    return {
        "current_agent": OTK_HEAD_AGENT_ID,
        "next_agent": OTK_HEAD_AGENT_ID,
        "quality_stage": "quality_queued",
        "stage": "handoff_quality",
        "status": "handed_to_quality",
        "case_status": ProcurementCaseStatus.QUALITY_QUEUED.value,
        "requires_human": False,
        "summary": (
            f"Кейс {state.get('case_number') or state.get('case_id')} передан в ОТК "
            f"(очередь входного контроля)."
        ),
    }


def require_human(state: ProcurementPipelineState) -> dict[str, Any]:
    return {
        "stage": "require_human",
        "status": "human_required",
        "case_status": ProcurementCaseStatus.HUMAN_REQUIRED.value,
        "requires_human": True,
        "summary": state.get("stop_reason")
        or "Для продолжения закупочной цепочки требуется решение человека.",
    }


def finalize(state: ProcurementPipelineState) -> dict[str, Any]:
    status = state.get("status") or "completed"
    case_status = state.get("case_status") or status
    if state.get("coverage_status") == "covered":
        summary = state.get("summary") or "Обеспеченность подтверждена, закупка не требуется."
        status = "completed"
    elif status == "handed_to_quality":
        summary = state.get("summary") or "Цепочка завершена передачей в ОТК."
        status = "completed"
    elif status in {"human_required", "rejected"} or state.get("requires_human"):
        summary = state.get("stop_reason") or state.get("summary") or "Требуется человек."
        status = "completed_with_issues"
    elif status == "failed":
        summary = state.get("stop_reason") or "Цепочка остановлена с ошибкой."
    else:
        summary = state.get("summary") or f"Статус: {status}"
    return {
        "stage": "finalize",
        "status": status,
        "case_status": case_status,
        "summary": summary,
        "requires_human": bool(state.get("requires_human")),
    }


def build_graph(*, checkpointer: Any | None = None):
    graph = StateGraph(ProcurementPipelineState)

    graph.add_node("ingest_need", ingest_need)
    graph.add_node("assign_picker", assign_picker)
    graph.add_node("check_coverage", check_coverage)
    graph.add_node("match_suppliers", match_suppliers)
    graph.add_node("draft_po", draft_po)
    graph.add_node("await_approval", await_approval)
    graph.add_node("handoff_manager", handoff_manager)
    graph.add_node("handoff_quality", handoff_quality)
    graph.add_node("require_human", require_human)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "ingest_need")
    graph.add_conditional_edges(
        "ingest_need",
        _after_ingest,
        {
            "assign_picker": "assign_picker",
            "require_human": "require_human",
            "finalize": "finalize",
        },
    )
    graph.add_edge("assign_picker", "check_coverage")
    graph.add_conditional_edges(
        "check_coverage",
        _after_coverage,
        {
            "match_suppliers": "match_suppliers",
            "require_human": "require_human",
            "finalize": "finalize",
        },
    )
    graph.add_conditional_edges(
        "match_suppliers",
        _after_match,
        {"draft_po": "draft_po", "require_human": "require_human"},
    )
    graph.add_conditional_edges(
        "draft_po",
        _after_draft,
        {"await_approval": "await_approval", "require_human": "require_human"},
    )
    graph.add_conditional_edges(
        "await_approval",
        _after_approval,
        {"handoff_manager": "handoff_manager", "require_human": "require_human"},
    )
    graph.add_edge("handoff_manager", "handoff_quality")
    graph.add_edge("handoff_quality", "finalize")
    graph.add_edge("require_human", "finalize")
    graph.add_edge("finalize", END)

    # LangGraph API / Studio rejects custom checkpointers on the exported graph.
    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


# Studio / langgraph.json entrypoint — no custom checkpointer.
procurement_pipeline_graph = build_graph()


__all__ = [
    "ProcurementPipelineState",
    "build_graph",
    "procurement_pipeline_graph",
    "ingest_need",
    "assign_picker",
    "check_coverage",
    "match_suppliers",
    "draft_po",
    "await_approval",
    "handoff_manager",
    "handoff_quality",
    "require_human",
    "finalize",
]
