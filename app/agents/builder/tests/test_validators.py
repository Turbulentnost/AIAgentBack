from __future__ import annotations

from app.agents.builder.llm import (
    merge_required_elements,
    normalize_string_list,
    pending_questions_for_elements,
)
from app.agents.builder.validators import validate_required_elements


def test_normalize_string_list_from_dict_workflow_steps():
    value = [
        {"title": "Определение города", "description": "Уточнить город пользователя"},
        {"title": "Получение данных", "description": "Запрос к API погоды"},
    ]
    result = normalize_string_list(value)
    assert result == [
        "Определение города: Уточнить город пользователя",
        "Получение данных: Запрос к API погоды",
    ]


def test_validate_required_elements_all_filled():
    requirements = {
        "required_elements": [
            {"key": "city", "label": "Город", "required": True, "status": "filled", "value": "Москва"},
            {"key": "format", "label": "Формат", "required": True, "status": "filled", "value": "краткий"},
        ]
    }
    result = validate_required_elements(requirements)
    assert result["valid"] is True
    assert result["missing"] == []


def test_merge_required_elements_preserves_filled_when_llm_resends_pending():
    existing = [
        {"key": "sites", "label": "Сайты", "value": "любые", "status": "filled"},
    ]
    incoming = [{"key": "sites", "label": "Сайты", "status": "pending"}]
    result = merge_required_elements(existing, incoming)
    assert result[0]["status"] == "filled"
    assert result[0]["value"] == "любые"


def test_pending_questions_for_elements_skips_filled():
    elements = [
        {"key": "sites", "label": "Сайты", "question": "Какие сайты?", "status": "filled", "value": "любые"},
        {"key": "format", "label": "Формат", "question": "Какой формат?", "status": "pending"},
    ]
    questions = pending_questions_for_elements(elements)
    assert questions == ["Какой формат?"]


def test_validate_required_elements_status_filled_without_value_is_missing():
    requirements = {
        "required_elements": [
            {"key": "sites", "label": "Сайты", "required": True, "status": "filled"},
        ]
    }
    result = validate_required_elements(requirements)
    assert result["valid"] is False
    assert "Сайты" in result["missing"]


def test_validate_required_elements_missing():
    requirements = {
        "required_elements": [
            {"key": "city", "label": "Город", "required": True, "status": "pending"},
            {"key": "format", "label": "Формат ответа", "required": True, "value": "краткий"},
        ]
    }
    result = validate_required_elements(requirements)
    assert result["valid"] is False
    assert "Город" in result["missing"]
