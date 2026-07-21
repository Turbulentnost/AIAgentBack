"""Граф LangGraph агента начальника ОМТО (раздел 6, Приложение А ТЗ-АГТ-ОМТО-НАЧ-001).

Топология: входная валидация → загрузка контекста 1С и контроль качества данных →
надзорные проверки (SLA, цена, поставка, претензии) → свод и классификация критичности →
расчёт уверенности → проект решения ИЛИ проект эскалации → проект ежедневного отчёта →
точка HITL → структурированный результат.

В проде импорт заменяется на ``from langgraph.graph import StateGraph, START, END``
(API-совместимо). Проверки check_* в ТЗ помечены «параллельно» — в проде они выполняются
в рамках ОДНОГО суперштага LangGraph; здесь для мини-runtime реализованы последовательной
цепочкой (топология эквивалентна). Точки HITL исполняются через interrupt + checkpointer
(PostgreSQL). Здесь используется совместимый мини-runtime для end-to-end на мок-данных.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph, START, END  # боевой движок (мини-рантайм graph_runtime — совместимая замена)

from . import AGENT_ID
from .state import OmtoHeadState
from . import nodes as N


# ---- роутеры ветвлений ------------------------------------------------------

def _route_by_validity(state: dict[str, Any]) -> str:
    """После validate_input: невалидная задача → сразу расчёт уверенности; иначе — поток."""

    if any(e.get("type") == "missing_field" for e in state.get("errors", [])):
        return "invalid"
    return "ok"


def _route_by_severity(state: dict[str, Any]) -> str:
    """После assess_confidence: critical → проект эскалации; иначе — проект решения."""

    if any(f.get("severity") == "critical" for f in state.get("findings") or []):
        return "critical"
    return "normal"


# ---- построение графа -------------------------------------------------------

def build_graph(checkpointer=None):
    """Собирает и компилирует граф агента. Возвращает исполняемый объект (.invoke)."""

    g = StateGraph(OmtoHeadState)

    # узлы (имена соответствуют TABLE 7 ТЗ, раздел 6.2)
    g.add_node("validate_input", N.validate_input)
    g.add_node("load_context_1c", N.load_context_1c)
    g.add_node("data_quality_check", N.data_quality_check)
    g.add_node("check_assignment_sla", N.check_assignment_sla)
    g.add_node("check_procurement_sla", N.check_procurement_sla)
    g.add_node("check_price_deviation", N.check_price_deviation)
    g.add_node("check_delivery_deviation", N.check_delivery_deviation)
    g.add_node("check_claims", N.check_claims)
    g.add_node("aggregate_findings", N.aggregate_findings)
    g.add_node("classify_severity", N.classify_severity)
    g.add_node("assess_confidence", N.assess_confidence_node)
    g.add_node("draft_decision_card", N.draft_decision_card)
    g.add_node("draft_escalation", N.draft_escalation)
    g.add_node("build_daily_report", N.build_daily_report)
    g.add_node("human_review", N.human_review)
    g.add_node("emit_result", N.emit_result)

    # вход и ветвление по валидности задачи
    g.add_edge(START, "validate_input")
    g.add_conditional_edges("validate_input", _route_by_validity, {
        "invalid": "assess_confidence",     # невалидная задача → уверенность → needs_input
        "ok": "load_context_1c",
    })

    # загрузка контекста и контроль качества данных (сквозной агент качества)
    g.add_edge("load_context_1c", "data_quality_check")

    # надзорные проверки (в проде — один суперштаг LangGraph; здесь — цепочка)
    g.add_edge("data_quality_check", "check_assignment_sla")
    g.add_edge("check_assignment_sla", "check_procurement_sla")
    g.add_edge("check_procurement_sla", "check_price_deviation")
    g.add_edge("check_price_deviation", "check_delivery_deviation")
    g.add_edge("check_delivery_deviation", "check_claims")

    # свод, классификация, уверенность
    g.add_edge("check_claims", "aggregate_findings")
    g.add_edge("aggregate_findings", "classify_severity")
    g.add_edge("classify_severity", "assess_confidence")

    # ветвление по критичности: проект эскалации ИЛИ проект решения
    g.add_conditional_edges("assess_confidence", _route_by_severity, {
        "critical": "draft_escalation",
        "normal": "draft_decision_card",
    })

    # отчётность формируется во всех ветках; для task_type=daily_report — основной выход
    g.add_edge("draft_escalation", "build_daily_report")
    g.add_edge("draft_decision_card", "build_daily_report")

    # точка HITL и результат
    g.add_edge("build_daily_report", "human_review")
    g.add_edge("human_review", "emit_result")
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
        "case_ids": task.get("case_ids", []),
        "period": task.get("period", {}),
    })
    return build_graph().invoke(state)


if __name__ == "__main__":  # dry-run на мок-данных
    import json

    demo = {
        "correlation_id": "00000000-0000-0000-0000-000000000000",
        "tenant_id": "example_tenant",
        "task_type": "periodic_control",
        "case_ids": ["ЗЗ-000412", "ЗЗ-000418"],
        "period": {"from": "2026-07-01", "to": "2026-07-16"},
        "requested_by": "user-demo",
    }
    final = run(demo)
    print(f"[{AGENT_ID}] result:")
    print(json.dumps(final.get("result", {}), ensure_ascii=False, indent=2))
