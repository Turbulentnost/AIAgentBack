"""Узлы загрузки контекста и проверки качества данных.

Все обращения к 1С:ERP — через шину инструментов platform_sdk (мок-заглушки; данные
фиктивные). Прямое обращение агента к 1С запрещено (п. 4.5 ТЗ-ПЛАТФ-001). Реальные вызовы
помечены TODO(integration) в platform_sdk.
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import call_tool, source_ref

from .. import AGENT_ID


def _cid(state: dict[str, Any]) -> str:
    return state.get("correlation_id", "")


def load_context_1c(state: dict[str, Any]) -> dict[str, Any]:
    """Загрузка кейсов, заявок, заказов, счетов, поставок, претензий (тип «инструмент»).

    Считывает через integration-service данные по кейсам области проверки: очередь заявок
    и назначения (Ф1), даты этапов закупки (Ф2), проектные цены (Ф3), статусы счетов/оплат/
    поставок (Ф4), претензии (Ф4). Каждый объект сопровождается версией/датой изменения для
    последующей сверки. При недоступности шлюза 1С — частичный результат с понижением
    уверенности (отказоустойчивость, T8).
    """

    case_ids = state.get("case_ids") or []
    call_tool(AGENT_ID, "erp.read_procurement_cases", _cid(state), case_ids=case_ids,
              period=state.get("period"))
    call_tool(AGENT_ID, "erp.read_project_prices", _cid(state), case_ids=case_ids)
    call_tool(AGENT_ID, "erp.read_invoices_payments", _cid(state), case_ids=case_ids)
    call_tool(AGENT_ID, "erp.read_delivery_status", _cid(state), case_ids=case_ids)
    call_tool(AGENT_ID, "erp.read_claims", _cid(state), case_ids=case_ids)

    # Нормализованный снимок 1С:ERP (мок: без отклонений). В проде — данные integration-service.
    erp_snapshot: dict[str, Any] = {
        "cases": [{"case_id": cid, "assigned": True, "assignment_overdue": False,
                   "procurement_overdue": False, "price_deviation_pct": 0.0,
                   "project_price_valid": True, "delivery_deviation": False,
                   "claim_overdue": False} for cid in case_ids],
        "loaded": True,
    }
    refs = [source_ref("СТО-28-020", section="7.2", clause="объекты 1С в области видимости",
                       version="07", accessed_at=state.get("_today", ""), status="проверенный")]
    return {"erp_snapshot": erp_snapshot, "source_references": refs}


def data_quality_check(state: dict[str, Any]) -> dict[str, Any]:
    """Обращение к сквозному агенту качества данных платформы (тип «внешний агент»).

    Проверяет полноту и непротиворечивость снимка 1С:ERP (например, согласованность статуса
    оплаты и реестра платежей). При обнаружении противоречия — регистрируется ошибка
    ``source_conflict`` (понижение уверенности узлом assess_confidence, раздел 8.2 ТЗ).
    Обращение к сквозному агенту выполняется через оркестратор (мок через notify.route).
    """

    # TODO(integration): вызов сквозного агента качества данных через оркестратор.
    call_tool(AGENT_ID, "notify.route", _cid(state), target="data_quality_agent",
              case_ids=state.get("case_ids") or [])
    snapshot = state.get("erp_snapshot") or {}
    errors: list[dict[str, Any]] = []
    if not snapshot.get("loaded"):
        # Недоступность/неполнота данных 1С → частичный результат, уверенность понижается.
        errors.append({"type": "source_conflict", "detail": "снимок 1С неполон/недоступен"})
    return {"errors": errors}
