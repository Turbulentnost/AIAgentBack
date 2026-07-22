"""Узлы загрузки контекста и данных (чтение из 1С/RAG, классификация позиции).

Все обращения — через шину инструментов platform_sdk (мок-заглушки; данные фиктивные).
Реальные вызовы помечены TODO(integration) в platform_sdk.
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import call_tool, hybrid_search, llm_complete, source_ref

from .. import AGENT_ID


def _cid(state: dict[str, Any]) -> str:
    return state.get("correlation_id", "")


def load_queue_1c(state: dict[str, Any]) -> dict[str, Any]:
    """Ф1. Загрузка очереди нераспределённых позиций из 1С:ERP (тип «инструмент»).

    Считывает заявки с пустым реквизитом «Ответственный», обогащает номенклатурной
    группой, датой потребности, ЦФО, приоритетом; рассчитывает время в очереди и
    сопоставляет с допустимым сроком (сигнал при превышении — K2).
    """

    call_tool(AGENT_ID, "erp.read_unassigned_queue", _cid(state),
              position_ids=state.get("position_ids", []))
    # Мок-очередь: одна позиция с полным набором реквизитов.
    queue = [{
        "position_id": (state.get("position_ids") or ["ЗЗ-000001"])[0],
        "item_group": "метизы",
        "need_date": "2026-08-12",
        "cfo": "ЦФО-01",
        "priority": state.get("priority_hint", "normal"),
        "in_queue_days": 1,
        "overdue": False,
    }]
    refs = [source_ref("СТО-28-020", section="шаг 2.1", clause="ведение очереди",
                       version="07", accessed_at=state.get("_today", ""),
                       status="проверенный")]
    return {"queue": queue, "source_references": refs}


def load_managers_profile(state: dict[str, Any]) -> dict[str, Any]:
    """Ф4. Профиль менеджеров: специализация, загрузка, доступность (тип «инструмент»).

    Считывает текущую загрузку (открытые позиции, взвешенные по сложности/срочности) и
    доступность (отпуск/больничный/командировка из конфигурации арендатора).
    """

    call_tool(AGENT_ID, "erp.read_managers_workload", _cid(state))
    managers = [
        {"id": "MGR-A", "name": "Иванов И.И.", "specializations": ["метизы"],
         "workload_pct": 84, "available": True, "workload_stale": False},
        {"id": "MGR-B", "name": "Петров П.П.", "specializations": ["метизы", "крепёж"],
         "workload_pct": 96, "available": True, "workload_stale": False},
    ]
    return {"managers": managers}


def classify_position(state: dict[str, Any]) -> dict[str, Any]:
    """Ф3. Классификация позиции: номенклатурная группа, срочность, приоритет (LLM + справочник).

    Классифицирует по справочнику номенклатурных групп арендатора (конфигурация, не
    жёсткий код), определяет срочность по дате потребности и критичность по влиянию на
    производственный план. При неоднозначной группе уверенность понижается (confidence.yaml).
    """

    call_tool(AGENT_ID, "erp.read_item_groups", _cid(state))
    hybrid_search("правила распределения позиций по номенклатурным группам",
                  collection="regulations",
                  full_text_terms=["СТО-28-020", "шаг 2.1"])
    llm_complete("Определить номенклатурную группу, срочность и приоритет позиции",
                 system="classify_position")

    item = (state.get("queue") or [{}])[0]
    classification = {
        "item_group": item.get("item_group", "метизы"),
        "group_unambiguous": True,        # мок: группа определяется однозначно
        "urgency": "normal",
        "priority": item.get("priority", state.get("priority_hint", "normal")),
        "critical_for_plan": False,
    }
    refs = [source_ref("СТО-28-020", section="шаг 2.1", clause="классификация позиции",
                       version="07", accessed_at=state.get("_today", ""),
                       status="проверенный")]
    return {"classification": classification, "source_references": refs}
