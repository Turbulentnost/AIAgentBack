"""Tests for converting AI check results into marking page_level."""

from __future__ import annotations

from app.gost.marking_from_check import (
    build_page_level_from_check,
    page_note_from_item,
    problem_report_from_payload,
)


def test_build_page_level_from_check_maps_page_errors() -> None:
    payload = {
        "items": [
            {
                "page": 11,
                "errors": [
                    {
                        "code": "missing_signature",
                        "message": "Нет подписи",
                        "gost_reference": "ГОСТ Р 2.104-2023",
                    }
                ],
                "warnings": [],
                "checks": [],
                "llm_report_text": "Замечание LLM по листу 11",
                "report_text": "VLM: элементы, позиции, ход обработки…",
            }
        ],
        "llm_summary": "Общий отчёт LLM",
        "report_text": "=== Общие предупреждения ===\nPipeline: extract → rules → openrouter",
        "summary": "Лист 11: 1 ошибок",
    }

    page_level, problem_report = build_page_level_from_check(payload)

    assert problem_report == "Общий отчёт LLM"
    assert len(page_level) == 1
    assert page_level[0]["page"] == 11
    assert page_level[0]["note"] == "Замечание LLM по листу 11"
    findings = page_level[0]["gost_findings"]
    assert len(findings) == 1
    assert findings[0]["gost_key"] == "2.104"
    assert findings[0]["severity"] == "error"
    assert findings[0]["note"] == "Нет подписи"


def test_build_page_level_from_check_ignores_vlm_and_progress_fields() -> None:
    payload = {
        "items": [
            {
                "page": 1,
                "errors": [],
                "warnings": [],
                "report_text": "Извлечено без оценки (Stage 1)",
                "summary": "Извлечено без оценки",
            }
        ],
        "report_text": "=== page.pdf ===\nПроверка комплекта…",
        "summary": "Pipeline: extract → rules → openrouter",
        "global_warnings": ["Pipeline: extract → rules → openrouter"],
    }

    page_level, problem_report = build_page_level_from_check(payload)

    assert problem_report == ""
    assert page_level == []


def test_page_note_and_problem_report_helpers_use_llm_fields_only() -> None:
    assert page_note_from_item({"report_text": "VLM", "llm_report_text": "LLM"}) == "LLM"
    assert problem_report_from_payload({"summary": "status", "llm_summary": "LLM doc"}) == "LLM doc"


def test_problem_report_filters_llm_meta_text() -> None:
    meta = (
        "Проверка комплекта КД выполнена на основе предоставленного индекса. "
        "В данных отсутствуют позиции (positions_in_spec, positions_on_drawing) "
        "и содержимое листов (sheets) для анализа перекрёстных ссылок. "
        "Нарушения нумерации или соответствия позиций не могут быть выявлены "
        "из-за отсутствия детализированных данных в индексе."
    )
    payload = {"llm_summary": meta, "items": []}
    page_level, problem_report = build_page_level_from_check(payload)
    assert problem_report == ""
    assert page_level == []


def test_page_note_filters_llm_meta_text() -> None:
    meta = "Проверка комплекта КД выполнена на основе предоставленного индекса."
    payload = {
        "items": [
            {
                "page": 3,
                "errors": [],
                "warnings": [],
                "llm_report_text": meta,
            }
        ]
    }
    page_level, _ = build_page_level_from_check(payload)
    assert page_level == []


def test_build_page_level_from_check_includes_package_errors_with_pages() -> None:
    payload = {
        "items": [],
        "package_errors": [
            {
                "code": "position_missing_in_bom",
                "severity": "error",
                "message": "Позиция 4 отсутствует в спецификации",
                "gost_reference": "ГОСТ 2.105",
                "pages": [2],
            }
        ],
    }

    page_level, _ = build_page_level_from_check(payload)

    assert len(page_level) == 1
    assert page_level[0]["page"] == 2
    assert page_level[0]["gost_findings"][0]["gost_key"] == "2.105"


def test_build_page_level_from_check_skips_internal_sheet_sequence_on_page() -> None:
    payload = {
        "items": [
            {
                "page": 1,
                "errors": [
                    {
                        "code": "sheet_sequence",
                        "message": "Разные значения sheets_total на листах: [2, 5].",
                        "gost_reference": "ГОСТ Р 2.104-2023",
                    }
                ],
                "warnings": [],
                "checks": [],
            }
        ],
    }

    page_level, _ = build_page_level_from_check(payload)

    assert page_level == []


def test_build_page_level_from_check_skips_document_level_package_errors() -> None:
    payload = {
        "items": [{"page": 1, "errors": [], "warnings": [], "checks": []}],
        "package_errors": [
            {
                "code": "sheet_sequence",
                "severity": "error",
                "message": "Разные значения sheets_total",
                "gost_reference": "ГОСТ Р 2.104-2023",
                "details": {"totals": [2, 5]},
            }
        ],
    }

    page_level, _ = build_page_level_from_check(payload)

    assert page_level == []
