"""Узлы идентификации и сбора сведений (чтение из 1С/внешних реестров, RAG, запрос реквизитов).

Все обращения — через шину инструментов platform_sdk (мок-заглушки; данные фиктивные).
Реальные вызовы помечены TODO(integration) в platform_sdk. Прямой доступ агента к 1С и
внешним ресурсам запрещён — только через ext.check_official_registries интеграционного слоя.

Узлы check_* соответствуют параллельным проверкам Ф2 (тип «инструмент / параллельно»).
В мини-runtime они реализованы последовательной цепочкой; в проде — один суперштаг LangGraph.
Ошибка одного источника не останавливает граф — формируется частичный результат (T8).
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import call_tool, hybrid_search, llm_complete, source_ref

from .. import AGENT_ID


def _cid(state: dict[str, Any]) -> str:
    return state.get("correlation_id", "")


def normalize_counterparty(state: dict[str, Any]) -> dict[str, Any]:
    """Ф1. Нормализация ИНН / ОГРН / наименования; идентификация карточки 1С (правила + инструмент).

    Поиск карточки контрагента по ИНН/ОГРН; определение признака «новый контрагент».
    При неоднозначной идентификации (нет ИНН / несколько карточек / расхождение наименования)
    признак ``ambiguous`` выставляется в true — далее маршрут на request_details.
    """

    counterparty = state.get("counterparty") or {}
    call_tool(AGENT_ID, "erp.read_counterparty_card", _cid(state),
              inn=counterparty.get("inn"), name=counterparty.get("name"))

    inn = counterparty.get("inn")
    # мок-идентификация: карточка найдена, если передан ИНН (в проде — поиск по OData)
    counterparty_id = "КА-000001" if inn else None
    ambiguous = not inn                       # нет ИНН → неоднозначная идентификация (Ф1)
    is_new = counterparty_id is None          # отсутствие карточки → новый контрагент

    return {
        "counterparty_id": counterparty_id,
        "is_new": is_new,
        "_ambiguous": ambiguous,
    }


def request_details(state: dict[str, Any]) -> dict[str, Any]:
    """Возврат закупщику при неоднозначной идентификации (тип LLM).

    Проверка не выполняется; формируется запрос реквизитов, data_confidence = low.
    Автоматическое дополнение существенных данных запрещено — только запрос уточнения.
    """

    llm_complete("Сформировать запрос недостающих реквизитов контрагента",
                 system="request_details", mask_pii=True)
    findings = [{
        "type": "identification_ambiguous",
        "severity": "major",
        "description": "Контрагент не идентифицируется однозначно "
                       "(отсутствует ИНН / несколько карточек / расхождение наименования)",
        "source": "Справочник.Контрагенты 1С; входная задача",
        "recommendation": "Запросить реквизиты у закупщика; проверка не выполняется",
    }]
    call_tool(AGENT_ID, "notify.route", _cid(state), target="procurement_manager_agent")
    return {"findings": findings, "requires_human_review": True,
            "errors": [{"type": "identification_ambiguous"}]}


def check_registry_1c(state: dict[str, Any]) -> dict[str, Any]:
    """Ф2. Карточка контрагента, реквизиты, договоры, взаиморасчёты (инструмент / параллельно)."""

    call_tool(AGENT_ID, "erp.read_counterparty_card", _cid(state),
              counterparty_id=state.get("counterparty_id"))
    call_tool(AGENT_ID, "erp.read_settlements", _cid(state),
              counterparty_id=state.get("counterparty_id"))
    registry_data = {"card": True, "contracts": [], "settlements": {"overdue": False}}
    refs = [source_ref("Справочник.Контрагенты", clause="карточка контрагента", version="1С:ERP",
                       accessed_at=state.get("_today", ""), status="проверенный")]
    return {"registry_data": registry_data, "source_references": refs}


def check_external_sources(state: dict[str, Any]) -> dict[str, Any]:
    """Ф2. Официальные реестры через интеграционный слой (инструмент / параллельно).

    Фиксация ссылки, даты обращения, версии и статуса источника. Прямой доступ агента
    к внешним ресурсам запрещён — только ext.check_official_registries.
    """

    call_tool(AGENT_ID, "ext.check_official_registries", _cid(state),
              inn=(state.get("counterparty") or {}).get("inn"))
    external_data = {"status": "active", "registered_months": 24, "source_status": "официальный"}
    refs = [source_ref("Официальный реестр", clause="сведения о регистрации", version="—",
                       accessed_at=state.get("_today", ""), status="официальный")]
    return {"external_data": external_data, "source_references": refs}


def check_history(state: dict[str, Any]) -> dict[str, Any]:
    """Ф2. Исполнение договоров, претензии, рекламации, просрочки (инструмент / параллельно)."""

    call_tool(AGENT_ID, "erp.read_supply_history", _cid(state),
              counterparty_id=state.get("counterparty_id"))
    call_tool(AGENT_ID, "erp.read_claims", _cid(state),
              counterparty_id=state.get("counterparty_id"))
    supply_history = {"contracts_executed": 0, "claims": 0, "overdue_supplies": 0}
    return {"supply_history": supply_history}


def check_affiliation(state: dict[str, Any]) -> dict[str, Any]:
    """Ф2. Связанность с иными контрагентами и сотрудниками, конфликт интересов (инструмент)."""

    call_tool(AGENT_ID, "erp.check_affiliation", _cid(state),
              counterparty_id=state.get("counterparty_id"))
    affiliation = {"related": False, "conflict_of_interest": False, "confirmed": True}
    return {"affiliation": affiliation}
