"""Граф LangGraph агента сотрудника службы безопасности (раздел 6, Приложение А ТЗ-АГТ-СБ-001).

Топология: входная валидация → идентификация → (при неоднозначности — запрос реквизитов) →
параллельные проверки (1С / внешние реестры / история / связанность) → сопоставление с
критериями → (при неполноте — перечень пробелов) → оценка риска → проект заключения по
уровню риска → HITL (утверждение) → фиксация вердикта → расчёт уверенности → результат.

В проде импорт заменяется на ``from langgraph.graph import StateGraph, START, END``
(API-совместимо). Точки HITL исполняются через interrupt + checkpointer (PostgreSQL).
Здесь используется совместимый мини-runtime, чтобы граф проходил end-to-end (мок-данные).

Узлы check_* соответствуют параллельным проверкам Ф2 («инструмент / параллельно»). В мини-
runtime они собраны последовательной цепочкой; в проде — один суперштаг LangGraph (fan-out /
fan-in). Ошибка одного источника не роняет граф — формируется частичный результат (T8).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph, START, END  # боевой движок (мини-рантайм graph_runtime — совместимая замена)

from . import AGENT_ID
from .state import SecurityOfficerState
from . import nodes as N


# ---- роутеры ветвлений ------------------------------------------------------

def _route_after_validate(state: dict[str, Any]) -> str:
    """После validate_input: невалидная задача → результат; иначе — идентификация."""

    if any(e.get("type") == "missing_field" for e in state.get("errors", [])):
        return "invalid"
    return "ok"


def _route_after_identify(state: dict[str, Any]) -> str:
    """Неоднозначная идентификация (нет ИНН / несколько карточек / расхождение) → запрос реквизитов."""

    return "ambiguous" if state.get("_ambiguous") else "ok"


def _route_after_criteria(state: dict[str, Any]) -> str:
    """Покрытие обязательных критериев < 100% → перечень пробелов; иначе — оценка риска."""

    return "gaps" if float(state.get("coverage", 0)) < 100.0 else "ok"


def _route_by_risk(state: dict[str, Any]) -> str:
    """high → ОТКАЗ; medium → УСЛОВИЯ ДОПУСКА; low → СОГЛАСОВАНИЕ."""

    return {"high": "reject", "medium": "conditional", "low": "approve"}.get(
        state.get("risk_level"), "approve"
    )


# ---- построение графа -------------------------------------------------------

def build_graph(checkpointer=None):
    """Собирает и компилирует граф агента. Возвращает исполняемый объект (.invoke)."""

    g = StateGraph(SecurityOfficerState)

    # узлы (имена соответствуют таблице узлов раздела 6.2 / TABLE 7 ТЗ)
    g.add_node("validate_input", N.validate_input)
    g.add_node("normalize_counterparty", N.normalize_counterparty)
    g.add_node("request_details", N.request_details)
    g.add_node("check_registry_1c", N.check_registry_1c)
    g.add_node("check_external_sources", N.check_external_sources)
    g.add_node("check_history", N.check_history)
    g.add_node("check_affiliation", N.check_affiliation)
    g.add_node("map_to_criteria", N.map_to_criteria)
    g.add_node("list_gaps", N.list_gaps)
    g.add_node("risk_scoring", N.risk_scoring)
    g.add_node("draft_conclusion_reject", N.draft_conclusion_reject)
    g.add_node("draft_conclusion_conditional", N.draft_conclusion_conditional)
    g.add_node("draft_conclusion_approve", N.draft_conclusion_approve)
    g.add_node("human_approve", N.human_approve)
    g.add_node("write_counterparty_verdict", N.write_counterparty_verdict)
    g.add_node("assess_confidence", N.assess_confidence_node)
    g.add_node("emit_result", N.emit_result)

    # вход и валидация
    g.add_edge(START, "validate_input")
    g.add_conditional_edges("validate_input", _route_after_validate, {
        "invalid": "assess_confidence",
        "ok": "normalize_counterparty",
    })

    # идентификация (Ф1): неоднозначность → запрос реквизитов (проверка не выполняется)
    g.add_conditional_edges("normalize_counterparty", _route_after_identify, {
        "ambiguous": "request_details",
        "ok": "check_registry_1c",
    })
    g.add_edge("request_details", "assess_confidence")

    # сбор сведений (Ф2) — параллельные проверки; в мини-runtime — последовательная цепочка,
    # в проде — один суперштаг LangGraph (fan-out / fan-in). Ошибка источника не роняет граф.
    g.add_edge("check_registry_1c", "check_external_sources")
    g.add_edge("check_external_sources", "check_history")
    g.add_edge("check_history", "check_affiliation")
    g.add_edge("check_affiliation", "map_to_criteria")

    # сопоставление с критериями (Ф3): покрытие < 100% → перечень пробелов, заключение не выдаётся
    g.add_conditional_edges("map_to_criteria", _route_after_criteria, {
        "gaps": "list_gaps",
        "ok": "risk_scoring",
    })
    g.add_edge("list_gaps", "assess_confidence")

    # оценка риска (Ф4) → проект заключения по уровню риска (Ф5)
    g.add_conditional_edges("risk_scoring", _route_by_risk, {
        "reject": "draft_conclusion_reject",
        "conditional": "draft_conclusion_conditional",
        "approve": "draft_conclusion_approve",
    })

    # все три ветви заключения сходятся в точку HITL; фиксация вердикта — только после неё
    g.add_edge("draft_conclusion_reject", "human_approve")
    g.add_edge("draft_conclusion_conditional", "human_approve")
    g.add_edge("draft_conclusion_approve", "human_approve")
    g.add_edge("human_approve", "write_counterparty_verdict")
    g.add_edge("write_counterparty_verdict", "assess_confidence")

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
        "counterparty": task.get("counterparty", {}),
        "trigger": task.get("trigger", ""),
        "case_id": task.get("case_id", ""),
    })
    return build_graph().invoke(state)


if __name__ == "__main__":  # dry-run на мок-данных
    import json

    demo = {
        "correlation_id": "00000000-0000-0000-0000-000000000000",
        "tenant_id": "example_tenant",
        "task_type": "new_supplier_check",
        "counterparty": {
            "inn": "7701234567",
            "ogrn": "1027700000000",
            "name": "ООО «Пример-Поставка»",
            "country": "RU",
        },
        "trigger": "new_supplier",
        "case_id": "CASE-2026-0777",
        "requested_by": "user-demo",
    }
    final = run(demo)
    print(f"[{AGENT_ID}] result:")
    print(json.dumps(final.get("result", {}), ensure_ascii=False, indent=2))
