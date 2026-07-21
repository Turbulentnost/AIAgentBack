"""Узлы загрузки контекста и данных (чтение из 1С/RAG, разбор КП).

Все обращения — через шину инструментов platform_sdk (мок-заглушки; данные фиктивные).
Реальные вызовы помечены TODO(integration) в platform_sdk.
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import call_tool, hybrid_search, llm_complete, source_ref

from .. import AGENT_ID


def _cid(state: dict[str, Any]) -> str:
    return state.get("correlation_id", "")


def validate_spec(state: dict[str, Any]) -> dict[str, Any]:
    """Ф1. RAG-проверка КД, описания, файлов, документов качества (шаг 2.2). LLM + RAG.

    Извлекает спецификацию и связанную КД, проверяет полноту и актуальность версии КД.
    При неполноте формирует перечень недостающих полей (spec_gaps).
    """

    call_tool(AGENT_ID, "erp.read_specification", _cid(state), position_id=state.get("position_id"))
    hybrid_search("КД по позиции", collection="kd",
                  full_text_terms=[str(state.get("position_id", ""))])
    llm_complete("Проверить полноту и однозначность спецификации", system="validate_spec")

    spec: dict[str, Any] = {"position_id": state.get("position_id"), "kd_actual": True}
    gaps: list[str] = []  # мок: пробелов нет
    refs = [source_ref("СТО-10-095", clause="документы качества", version="—",
                       accessed_at=state.get("_today", ""), status="проверенный")]
    return {"spec": spec, "spec_gaps": gaps, "source_references": refs}


def request_clarification(state: dict[str, Any]) -> dict[str, Any]:
    """Запрос уточнения инициатору / КБ; цикл уточнения (тип LLM).

    Автоматическое дополнение существенных данных запрещено — формируется запрос полей.
    """

    gaps = state.get("spec_gaps") or []
    findings = [{
        "type": "spec_clarification_required",
        "severity": "major",
        "description": f"Недостающие поля спецификации: {', '.join(gaps) or '—'}",
        "source": "СТО-28-020 шаг 2.2; спецификация позиции",
        "recommendation": "Запросить уточнение у инициатора / КБ",
    }]
    call_tool(AGENT_ID, "notify.route", _cid(state), target="initiator", gaps=gaps)
    return {"findings": findings, "requires_human_review": True}


def search_suppliers(state: dict[str, Any]) -> dict[str, Any]:
    """Ф2. Поиск основного/дополнительного поставщика и рынка (шаг 2.3). Инструмент."""

    call_tool(AGENT_ID, "market.search_offers", _cid(state), position_id=state.get("position_id"))
    suppliers = [
        {"id": "SUP-A", "is_new": False, "rating": None, "has_contract": True},
        {"id": "SUP-B", "is_new": False, "rating": None, "has_contract": True},
    ]
    return {"suppliers": suppliers}


def load_supplier_history_1c(state: dict[str, Any]) -> dict[str, Any]:
    """Рейтинг, договор, качество, история поставок (инструмент)."""

    call_tool(AGENT_ID, "erp.read_supplier_history", _cid(state))
    call_tool(AGENT_ID, "erp.read_contracts", _cid(state))
    return {}


def evaluate_suppliers(state: dict[str, Any]) -> dict[str, Any]:
    """Формирование short-list с объяснимыми критериями (LLM + правила)."""

    llm_complete("Сформировать short-list с обоснованием критериев", system="evaluate_suppliers")
    refs = [source_ref("СТО-28-020", clause="шаг 2.3", version="07",
                       accessed_at=state.get("_today", ""), status="проверенный")]
    return {"source_references": refs}


def collect_quotes(state: dict[str, Any]) -> dict[str, Any]:
    """Ф6. Загрузка и разбор КП: PDF/DOCX/XLSX/сканы (document-service)."""

    for q in state.get("quotes", []) or []:
        call_tool(AGENT_ID, "docs.parse_quote", _cid(state), document_id=q.get("document_id"))
    return {}
