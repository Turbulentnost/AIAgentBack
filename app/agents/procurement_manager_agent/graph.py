"""Граф LangGraph агента менеджера по закупкам (раздел 6, Приложение А ТЗ-АГТ-ЗАКУП-001).

Топология: входная валидация → диспетчеризация по task_type в предметную ветвь →
проверки/проекты/точки HITL → расчёт уверенности → структурированный результат.

В проде импорт заменяется на ``from langgraph.graph import StateGraph, START, END``
(API-совместимо). Точки HITL исполняются через interrupt + checkpointer (PostgreSQL).
Здесь используется совместимый мини-runtime, чтобы граф проходил end-to-end (мок-данные).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph, START, END  # боевой движок (мини-рантайм graph_runtime — совместимая замена)

from . import AGENT_ID
from .state import ProcurementManagerState
from . import nodes as N


# ---- роутеры ветвлений ------------------------------------------------------

def _route_by_task(state: dict[str, Any]) -> str:
    """После validate_input: невалидная задача → результат; иначе — ветвь по task_type."""

    if any(e.get("type") == "missing_field" for e in state.get("errors", [])):
        return "invalid"
    return state.get("task_type", "validate_spec")


def _route_after_spec(state: dict[str, Any]) -> str:
    return "gaps" if state.get("spec_gaps") else "ok"


def _route_lead_time(state: dict[str, Any]) -> str:
    lead = state.get("lead_time") or {}
    return "ok" if lead.get("feasible", True) else "alt"


# ---- построение графа -------------------------------------------------------

def build_graph(checkpointer=None):
    """Собирает и компилирует граф агента. Возвращает исполняемый объект (.invoke)."""

    g = StateGraph(ProcurementManagerState)

    # узлы (имена соответствуют таблице узлов раздела 6.2 ТЗ)
    g.add_node("validate_input", N.validate_input)
    g.add_node("validate_spec", N.validate_spec)
    g.add_node("request_clarification", N.request_clarification)
    g.add_node("search_suppliers", N.search_suppliers)
    g.add_node("load_supplier_history_1c", N.load_supplier_history_1c)
    g.add_node("evaluate_suppliers", N.evaluate_suppliers)
    g.add_node("check_lead_time", N.check_lead_time)
    g.add_node("build_alternatives", N.build_alternatives)
    g.add_node("route_to_sb_agent", N.route_to_sb_agent)
    g.add_node("route_to_kb_agent", N.route_to_kb_agent)
    g.add_node("draft_rfq", N.draft_rfq)
    g.add_node("human_send_rfq", N.human_send_rfq)
    g.add_node("collect_quotes", N.collect_quotes)
    g.add_node("normalize_and_compare", N.normalize_and_compare)
    g.add_node("draft_comparison_table", N.draft_comparison_table)
    g.add_node("human_select_supplier", N.human_select_supplier)
    g.add_node("draft_order_and_contract", N.draft_order_and_contract)
    g.add_node("link_invoice_1c", N.link_invoice_1c)
    g.add_node("draft_claim", N.draft_claim)
    g.add_node("assess_confidence", N.assess_confidence_node)
    g.add_node("emit_result", N.emit_result)

    # вход и диспетчеризация по task_type
    g.add_edge(START, "validate_input")
    g.add_conditional_edges("validate_input", _route_by_task, {
        "invalid": "assess_confidence",
        "validate_spec": "validate_spec",
        "select_supplier": "search_suppliers",
        "prepare_rfq": "draft_rfq",
        "compare_quotes": "collect_quotes",
        "prepare_order": "draft_order_and_contract",
        "prepare_claim": "draft_claim",
    })

    # ветвь validate_spec (шаг 2.2)
    g.add_conditional_edges("validate_spec", _route_after_spec, {
        "gaps": "request_clarification",
        "ok": "assess_confidence",
    })
    g.add_edge("request_clarification", "assess_confidence")

    # ветвь select_supplier (шаги 2.3–2.4, 2.8)
    g.add_edge("search_suppliers", "load_supplier_history_1c")
    g.add_edge("load_supplier_history_1c", "evaluate_suppliers")
    g.add_edge("evaluate_suppliers", "check_lead_time")
    g.add_conditional_edges("check_lead_time", _route_lead_time, {
        "ok": "route_to_sb_agent",
        "alt": "build_alternatives",
    })
    g.add_edge("build_alternatives", "route_to_sb_agent")
    g.add_edge("route_to_sb_agent", "assess_confidence")

    # ветвь prepare_rfq (шаг 2.6) — отправку выполняет человек
    g.add_edge("draft_rfq", "human_send_rfq")
    g.add_edge("human_send_rfq", "assess_confidence")

    # ветвь compare_quotes (шаг 2.7) — выбор выполняет человек
    g.add_edge("collect_quotes", "normalize_and_compare")
    g.add_edge("normalize_and_compare", "draft_comparison_table")
    g.add_edge("draft_comparison_table", "human_select_supplier")
    g.add_edge("human_select_supplier", "assess_confidence")

    # ветвь prepare_order (шаги 2.5, 3.1) — заказ размещает человек
    g.add_edge("draft_order_and_contract", "route_to_kb_agent")
    g.add_edge("route_to_kb_agent", "link_invoice_1c")
    g.add_edge("link_invoice_1c", "assess_confidence")

    # ветвь prepare_claim (шаги 3.3, претензия)
    g.add_edge("draft_claim", "assess_confidence")

    # свод и результат
    g.add_edge("assess_confidence", "emit_result")
    g.add_edge("emit_result", END)

    # checkpointer=postgres в проде (agent.yaml → runtime.checkpointer)
    return g.compile(checkpointer=checkpointer)


def run(task: dict[str, Any]) -> dict[str, Any]:
    """Удобный запуск графа по входной задаче. Возвращает финальное состояние."""

    from app.platform_sdk import make_base_state

    state = make_base_state(
        task.get("correlation_id", ""),
        task.get("tenant_id", ""),
        task_type=task.get("task_type", ""),
        requested_by=task.get("requested_by", ""),
    )
    state.update({
        "position_id": task.get("position_id", ""),
        "need_date": task.get("need_date", ""),
        "quantity": task.get("quantity", 0),
        "quotes": task.get("quotes", []),
    })
    return build_graph().invoke(state)


if __name__ == "__main__":  # dry-run на мок-данных
    import json

    demo = {
        "correlation_id": "00000000-0000-0000-0000-000000000000",
        "tenant_id": "example_tenant",
        "task_type": "compare_quotes",
        "position_id": "ЗЗ-001284",
        "need_date": "2026-08-12",
        "quantity": 100,
        "quotes": [{"document_id": "КП-2026-0412"}, {"document_id": "КП-2026-0418"}],
        "requested_by": "user-demo",
    }
    final = run(demo)
    print(f"[{AGENT_ID}] result:")
    print(json.dumps(final.get("result", {}), ensure_ascii=False, indent=2))
