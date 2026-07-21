"""Граф LangGraph агента заместителя начальника ОМТО (раздел 6, Приложение А ТЗ-АГТ-ОМТО-ЗАМ-001).

Топология: входная валидация → диспетчеризация по task_type → загрузка очереди/профиля →
контроль дублей → классификация и подбор специализации → балансировка загрузки →
проект назначения → точка HITL (подтверждение начальником ОМТО) → запись ответственного →
расчёт уверенности → структурированный результат.

В проде импорт заменяется на ``from langgraph.graph import StateGraph, START, END``
(API-совместимо). Точки HITL исполняются через interrupt + checkpointer (PostgreSQL).
Здесь используется совместимый мини-runtime, чтобы граф проходил end-to-end (мок-данные).

ВАЖНО — открытая коллизия D01 (СТО-28-020 vs СТО-06-010): владелец создания заказа
поставщику не определён. До закрытия D01 граф формирует ТОЛЬКО проект назначения и
запись реквизита «Ответственный» (после HITL). Заказ поставщику НЕ создаётся, документы
не проводятся.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph, START, END  # боевой движок (мини-рантайм graph_runtime — совместимая замена)

from . import AGENT_ID
from .state import OmtoDeputyState
from . import nodes as N


# ---- роутеры ветвлений ------------------------------------------------------

def _route_by_task(state: dict[str, Any]) -> str:
    """После validate_input: невалидная задача → результат; иначе — ветвь по task_type."""

    if any(e.get("type") == "missing_field" for e in state.get("errors", [])):
        return "invalid"
    return state.get("task_type", "assign_positions")


def _route_after_duplicates(state: dict[str, Any]) -> str:
    """Дубль → связывание с кейсом; queue_review завершается; иначе — подбор менеджера."""

    if state.get("duplicates"):
        return "dup"
    if state.get("task_type") == "queue_review":
        return "review_done"
    return "assign"


def _route_after_managers(state: dict[str, Any]) -> str:
    """rebalance_workload → сразу балансировка; иначе — классификация позиции."""

    return "rebalance" if state.get("task_type") == "rebalance_workload" else "classify"


def _route_after_match(state: dict[str, Any]) -> str:
    """Нет однозначной специализации/кандидата → эскалация; иначе — балансировка."""

    return "ok" if (state.get("classification") or {}).get("has_candidate") else "escalate"


# ---- построение графа -------------------------------------------------------

def build_graph(checkpointer=None):
    """Собирает и компилирует граф агента. Возвращает исполняемый объект (.invoke)."""

    g = StateGraph(OmtoDeputyState)

    # узлы (имена соответствуют таблице узлов раздела 6.2 ТЗ / TABLE 7)
    g.add_node("validate_input", N.validate_input)
    g.add_node("load_queue_1c", N.load_queue_1c)
    g.add_node("check_duplicates", N.check_duplicates)
    g.add_node("link_to_existing_case", N.link_to_existing_case)
    g.add_node("load_managers_profile", N.load_managers_profile)
    g.add_node("classify_position", N.classify_position)
    g.add_node("match_specialization", N.match_specialization)
    g.add_node("balance_workload", N.balance_workload)
    g.add_node("escalate_to_head", N.escalate_to_head)
    g.add_node("draft_assignment", N.draft_assignment)
    g.add_node("assess_confidence", N.assess_confidence_node)
    g.add_node("human_confirm", N.human_confirm)
    g.add_node("writeback_1c", N.writeback_1c)
    g.add_node("emit_result", N.emit_result)

    # вход и диспетчеризация по task_type
    g.add_edge(START, "validate_input")
    g.add_conditional_edges("validate_input", _route_by_task, {
        "invalid": "assess_confidence",
        "assign_positions": "load_queue_1c",
        "queue_review": "load_queue_1c",
        "rebalance_workload": "load_managers_profile",
    })

    # очередь и контроль дублей (Ф1, Ф2)
    g.add_edge("load_queue_1c", "check_duplicates")
    g.add_conditional_edges("check_duplicates", _route_after_duplicates, {
        "dup": "link_to_existing_case",
        "review_done": "assess_confidence",
        "assign": "load_managers_profile",
    })
    g.add_edge("link_to_existing_case", "assess_confidence")

    # профиль менеджеров, классификация и подбор специализации (Ф3, Ф4)
    g.add_conditional_edges("load_managers_profile", _route_after_managers, {
        "rebalance": "balance_workload",
        "classify": "classify_position",
    })
    g.add_edge("classify_position", "match_specialization")
    g.add_conditional_edges("match_specialization", _route_after_match, {
        "escalate": "escalate_to_head",
        "ok": "balance_workload",
    })
    g.add_edge("escalate_to_head", "assess_confidence")

    # балансировка, проект назначения и точка HITL (Ф4, Ф5)
    # D01: формируется ТОЛЬКО проект назначения; заказ поставщику не создаётся.
    g.add_edge("balance_workload", "draft_assignment")
    g.add_edge("draft_assignment", "human_confirm")
    g.add_edge("human_confirm", "writeback_1c")
    g.add_edge("writeback_1c", "assess_confidence")

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
        "position_ids": task.get("position_ids", []),
        "priority_hint": task.get("priority_hint", "normal"),
    })
    return build_graph().invoke(state)


if __name__ == "__main__":  # dry-run на мок-данных
    import json

    demo = {
        "correlation_id": "00000000-0000-0000-0000-000000000000",
        "tenant_id": "example_tenant",
        "task_type": "assign_positions",
        "position_ids": ["ЗЗ-001284"],
        "priority_hint": "high",
        "requested_by": "user-demo",
    }
    final = run(demo)
    print(f"[{AGENT_ID}] result:")
    print(json.dumps(final.get("result", {}), ensure_ascii=False, indent=2))
