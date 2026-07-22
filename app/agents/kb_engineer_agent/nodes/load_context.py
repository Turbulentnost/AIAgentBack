"""Узлы загрузки контекста и данных: КД/ТД из RAG+1С, требования, данные аналога.

Все обращения — через шину инструментов platform_sdk (мок-заглушки; данные фиктивные).
Реальные вызовы помечены TODO(integration) в platform_sdk.
"""

from __future__ import annotations

from typing import Any

from app.platform_sdk import call_tool, hybrid_search, llm_complete, source_ref

from .. import AGENT_ID


def _cid(state: dict[str, Any]) -> str:
    return state.get("correlation_id", "")


def load_kd_rag(state: dict[str, Any]) -> dict[str, Any]:
    """Ф1. Гибридный поиск КД/ТД: вектор (смысл) + полнотекст (обозначение, пункт, версия).

    RAG-контур + чтение карточки позиции и связанных версий КД из 1С:ERP. Тип «RAG + инструмент».
    """

    hybrid_search(
        "КД/ТД по позиции: обязательные технические характеристики",
        collection="kd",
        full_text_terms=[str(state.get("position_id", "")), str(state.get("kd_ref", ""))],
    )
    call_tool(AGENT_ID, "erp.read_item_card", _cid(state), position_id=state.get("position_id"))
    call_tool(AGENT_ID, "erp.read_specification", _cid(state), position_id=state.get("position_id"))

    # Мок: КД найдена, обозначение/версия/статус заполнены заглушкой.
    kd: dict[str, Any] = {
        "kd_ref": state.get("kd_ref") or "КД-МОК",
        "version": "4.2",
        "status": "действующий",   # действующий|изменён|заменён|отменён|устаревший|требует проверки
        "found": True,
    }
    return {"kd": kd}


def check_kd_actuality(state: dict[str, Any]) -> dict[str, Any]:
    """Ф1. Определение статуса КД: действующий / изменён / заменён / отменён / устаревший.

    Тип «правила». При неактуальной или ненайденной КД — заключение НЕ выдаётся:
    data_confidence = low, requires_human_review = true, формируется запрос актуальной КД
    (раздел 8.2 ТЗ). Иначе фиксируется source_reference на КД.
    """

    kd = state.get("kd") or {}
    actual_statuses = ("действующий", "изменён")
    is_actual = bool(kd.get("found")) and kd.get("status") in actual_statuses

    if not is_actual:
        finding = {
            "type": "kd_not_actual",
            "severity": "critical",
            "description": (
                "КД по позиции не найдена или имеет статус "
                f"'{kd.get('status', 'не найдена')}'; заключение не выдаётся"
            ),
            "source": f"КД {kd.get('kd_ref', '—')}, версия {kd.get('version', '—')}; "
                      "СТО-28-020 (версия 07), шаг 2.5",
            "recommendation": "Запросить актуальную КД у КБ; заключение не формируется",
        }
        call_tool(AGENT_ID, "notify.route", _cid(state), target="kb_engineer",
                  reason="request_actual_kd")
        return {
            "kd_actual": False,
            "findings": [finding],
            "errors": [{"type": "kd_not_actual", "kd_ref": kd.get("kd_ref")}],
            "requires_human_review": True,
        }

    ref = source_ref(
        kd.get("kd_ref", "КД"),
        clause="статус актуальности",
        version=str(kd.get("version", "")),
        accessed_at=state.get("_today", ""),
        status="официальный",
    )
    return {"kd_actual": True, "source_references": [ref]}


def extract_requirements(state: dict[str, Any]) -> dict[str, Any]:
    """Ф2. Извлечение обязательных технических характеристик + source_references. LLM + RAG.

    Разметка каждой характеристики как обязательной или справочной по правилам арендатора;
    для внешних нормативов (ГОСТ) фиксируются ссылка, дата обращения, версия и статус.
    """

    call_tool(AGENT_ID, "rag.search_gost", _cid(state), position_id=state.get("position_id"))
    llm_complete("Извлечь обязательные технические характеристики из КД/ТД и ГОСТ",
                 system="extract_requirements")

    requirements = [
        {"key": "operating_temperature", "mandatory": True, "value": "-40...+70 °C"},
        {"key": "ingress_protection", "mandatory": True, "value": "IP65"},
    ]
    refs = [source_ref("ГОСТ (применимый)", clause="требования к позиции", version="—",
                       accessed_at=state.get("_today", ""), status="проверенный")]
    return {"requirements": requirements, "source_references": refs}


def load_analog_data(state: dict[str, Any]) -> dict[str, Any]:
    """Ф3. Разбор паспорта, сертификата, описания поставщика (document-service).

    Извлечение таблиц характеристик и реквизитов, нормализация единиц и условий измерения.
    При отсутствии обязательных данных — недостающие сведения формируются узлом list_missing_data.
    """

    analog_in = state.get("analog") or {}
    for doc_id in analog_in.get("document_ids", []) or []:
        call_tool(AGENT_ID, "docs.parse_analog_datasheet", _cid(state), document_id=doc_id)

    # Мок: характеристики аналога извлечены; недостающих обязательных данных нет.
    analog: dict[str, Any] = {
        "supplier_id": analog_in.get("supplier_id"),
        "item_name": analog_in.get("item_name"),
        "characteristics": {
            "operating_temperature": "-20...+70 °C",   # уже, чем требуется по КД
            "ingress_protection": "IP65",
        },
        "source_status": "проверенный",
    }
    missing: list[str] = []   # мок: обязательные данные аналога присутствуют
    return {"analog": analog, "missing_data": missing}


def list_missing_data(state: dict[str, Any]) -> dict[str, Any]:
    """Ф3. Перечень недостающих сведений по аналогу в адрес закупщика (правила).

    При отсутствии обязательных данных заключение не выдаётся: понижение уверенности и
    передача человеку; перечень маршрутизируется закупщику (раздел 8.2 ТЗ).
    """

    missing = state.get("missing_data") or []
    finding = {
        "type": "analog_data_incomplete",
        "severity": "major",
        "description": f"Недостающие обязательные данные аналога: {', '.join(missing) or '—'}",
        "source": "Паспорт/сертификат аналога; СТО-28-020 (версия 07), шаг 2.5",
        "recommendation": "Запросить недостающие сведения у закупщика; заключение не выдаётся",
    }
    call_tool(AGENT_ID, "notify.route", _cid(state), target="procurement_manager_agent",
              missing=missing)
    return {
        "findings": [finding],
        "errors": [{"type": "analog_data_incomplete", "fields": missing}],
        "requires_human_review": True,
    }
