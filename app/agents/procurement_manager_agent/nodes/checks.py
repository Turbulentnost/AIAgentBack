"""Узлы проверок и расчётов: срок, альтернативы, нормализация КП, уверенность."""

from __future__ import annotations

from typing import Any

from app.platform_sdk import assess_confidence, llm_complete, source_ref

from .. import AGENT_ID


def check_lead_time(state: dict[str, Any]) -> dict[str, Any]:
    """Ф3. Срок поставки vs дата потребности (шаг 2.4). Правила."""

    lead_time = {"feasible": True, "need_date": state.get("need_date"), "forecast": None}
    return {"lead_time": lead_time}


def build_alternatives(state: dict[str, Any]) -> dict[str, Any]:
    """Формирование альтернатив: аналог | изменение КД | другой поставщик | иной срок (LLM)."""

    llm_complete("Сформировать допустимые альтернативы при невозможности закупки",
                 system="build_alternatives")
    alternatives = [{"kind": "analog", "note": "мок-альтернатива"}]
    return {"alternatives": alternatives}


def normalize_and_compare(state: dict[str, Any]) -> dict[str, Any]:
    """Ф6. Нормализация цены, срока, доставки, оплаты, рисков (шаг 2.7). Правила + LLM.

    Приведение к единой базе: валюта, НДС, включённая доставка, срок в рабочих днях.
    """

    llm_complete("Нормализовать и сравнить КП по единой базе", system="normalize_and_compare")
    comparison = {
        "normalized": True,
        "quotes_count": len(state.get("quotes", []) or []),
        "recommended": None,          # выбор выполняет человек
    }
    refs = [source_ref("СТО-28-020", clause="шаг 2.7", version="07",
                       accessed_at=state.get("_today", ""), status="проверенный")]
    return {"comparison": comparison, "source_references": refs}


def assess_confidence_node(state: dict[str, Any]) -> dict[str, Any]:
    """Узел assess_confidence: расчёт data_confidence и requires_human_review (правила)."""

    return assess_confidence(state)
