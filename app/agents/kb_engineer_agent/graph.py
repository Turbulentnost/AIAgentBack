"""Граф LangGraph агента инженера КБ / ГСПП (раздел 6, Приложение А ТЗ-АГТ-КБ-001).

Топология: входная валидация → поднятие КД и проверка актуальности → извлечение
требований и разбор аналога → сопоставление/допустимость/риск → проект заключения
(запрет | разрешение) → HITL-утверждение → фиксация отклонения → уверенность → результат.

В проде импорт заменяется на ``from langgraph.graph import StateGraph, START, END``
(API-совместимо). Точки HITL исполняются через interrupt + checkpointer (PostgreSQL).
Здесь используется совместимый мини-runtime, чтобы граф проходил end-to-end (мок-данные).
Параллельные проверки раздела 6.2 в проде — один суперштаг LangGraph; в мини-runtime
реализованы последовательной цепочкой узлов.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph, START, END  # боевой движок (мини-рантайм graph_runtime — совместимая замена)

from . import AGENT_ID
from .state import KbEngineerState
from . import nodes as N


# ---- роутеры ветвлений ------------------------------------------------------

def _route_by_validity(state: dict[str, Any]) -> str:
    """После validate_input: невалидная задача → результат; иначе — поднятие КД."""

    if any(e.get("type") == "missing_field" for e in state.get("errors", [])):
        return "invalid"
    return "ok"


def _route_kd_actuality(state: dict[str, Any]) -> str:
    """Неактуальная/ненайденная КД → заключение не выдаётся (сразу на уверенность)."""

    return "ok" if state.get("kd_actual") else "not_actual"


def _route_analog(state: dict[str, Any]) -> str:
    """Нет обязательных данных аналога → перечень недостающих данных."""

    return "missing" if state.get("missing_data") else "ok"


def _route_verdict(state: dict[str, Any]) -> str:
    """Покрытие < 100% или безусловный запрет → «ЗАПРЕТ»; иначе «РАЗРЕШЕНИЕ»."""

    coverage = state.get("coverage") or 0.0
    prohibitive = (state.get("risk") or {}).get("prohibitive", False)
    if coverage < 100.0 or prohibitive:
        return "deny"
    return "allow"


# ---- построение графа -------------------------------------------------------

def build_graph(checkpointer=None):
    """Собирает и компилирует граф агента. Возвращает исполняемый объект (.invoke)."""

    g = StateGraph(KbEngineerState)

    # узлы (имена соответствуют таблице узлов раздела 6.2 / [TABLE 7] ТЗ)
    g.add_node("validate_input", N.validate_input)
    g.add_node("load_kd_rag", N.load_kd_rag)
    g.add_node("check_kd_actuality", N.check_kd_actuality)
    g.add_node("extract_requirements", N.extract_requirements)
    g.add_node("load_analog_data", N.load_analog_data)
    g.add_node("list_missing_data", N.list_missing_data)
    g.add_node("compare_characteristics", N.compare_characteristics)
    g.add_node("check_applicability", N.check_applicability)
    g.add_node("assess_risk", N.assess_risk)
    g.add_node("draft_conclusion_deny", N.draft_conclusion_deny)
    g.add_node("draft_conclusion_allow", N.draft_conclusion_allow)
    g.add_node("assess_confidence", N.assess_confidence_node)
    g.add_node("human_approve", N.human_approve)
    g.add_node("write_deviation_approval", N.write_deviation_approval)
    g.add_node("emit_result", N.emit_result)

    # вход и проверка валидности задачи
    g.add_edge(START, "validate_input")
    g.add_conditional_edges("validate_input", _route_by_validity, {
        "invalid": "assess_confidence",
        "ok": "load_kd_rag",
    })

    # Ф1. Поднятие КД и проверка актуальности
    g.add_edge("load_kd_rag", "check_kd_actuality")
    g.add_conditional_edges("check_kd_actuality", _route_kd_actuality, {
        "not_actual": "assess_confidence",     # заключение не выдаётся
        "ok": "extract_requirements",
    })

    # Ф2/Ф3. Требования и разбор аналога
    g.add_edge("extract_requirements", "load_analog_data")
    g.add_conditional_edges("load_analog_data", _route_analog, {
        "missing": "list_missing_data",
        "ok": "compare_characteristics",
    })
    g.add_edge("list_missing_data", "assess_confidence")   # заключение не выдаётся

    # Ф4. Сопоставление, допустимость, риск
    g.add_edge("compare_characteristics", "check_applicability")
    g.add_edge("check_applicability", "assess_risk")
    g.add_conditional_edges("assess_risk", _route_verdict, {
        "deny": "draft_conclusion_deny",
        "allow": "draft_conclusion_allow",
    })

    # Ф5. Проекты заключения
    g.add_edge("draft_conclusion_deny", "assess_confidence")   # ЗАПРЕТ — без записи отклонения
    g.add_edge("draft_conclusion_allow", "human_approve")      # РАЗРЕШЕНИЕ — через HITL
    g.add_edge("human_approve", "write_deviation_approval")    # фиксация только после HITL
    g.add_edge("write_deviation_approval", "assess_confidence")

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
        "kd_ref": task.get("kd_ref", ""),
        "analog": task.get("analog"),
        "_today": task.get("_today", ""),
    })
    return build_graph().invoke(state)


if __name__ == "__main__":  # dry-run на мок-данных
    import json

    demo = {
        "correlation_id": "00000000-0000-0000-0000-000000000000",
        "tenant_id": "example_tenant",
        "task_type": "analog_review",
        "position_id": "ЗЗ-001284",
        "kd_ref": "КД-4.2",
        "analog": {
            "supplier_id": "SUP-A",
            "item_name": "Датчик давления (аналог)",
            "document_ids": ["ПАСПОРТ-001", "СЕРТИФИКАТ-001"],
        },
        "requested_by": "user-demo",
    }
    final = run(demo)
    print(f"[{AGENT_ID}] result:")
    print(json.dumps(final.get("result", {}), ensure_ascii=False, indent=2))
