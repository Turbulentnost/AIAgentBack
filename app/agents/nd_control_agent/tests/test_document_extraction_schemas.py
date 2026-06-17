from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.schemas.nd_document_extraction import (
    DocumentExtractionResult,
    parse_document_extraction_result,
)


def _valid_payload() -> dict:
    return {
        "document": {
            "document_code": "И-15-230",
            "title": "Инструкция по входному контролю",
            "document_type": "instruction",
            "version": "2",
            "status": "active",
            "approval_date": "2024-03-15",
            "effective_date": "2024-04-01",
            "purpose": "Регламентирует входной контроль",
            "scope": {
                "text": "Применяется на всех площадках",
                "departments": ["Отдел качества"],
                "positions": ["Инженер ОТК"],
                "applies_to_all_company": False,
            },
        },
        "participants": {
            "developed_by": [
                {
                    "name": "Иванов И.И.",
                    "role": "Разработчик",
                    "department": "ОТК",
                    "date": "2024-03-01",
                    "evidence": {
                        "document_code": "И-15-230",
                        "page": 1,
                        "section": "Лист согласования",
                        "quote": "Разработал: Иванов И.И.",
                    },
                }
            ],
            "checked_by": [],
            "approved_by": [],
            "agreed_by": [],
        },
        "processes": [
            {
                "name": "Входной контроль материалов",
                "description": "Проверка поступающих материалов",
                "goal": "Исключить брак",
                "inputs": ["Партия материала"],
                "outputs": ["Акт входного контроля"],
                "actions": [
                    {
                        "action": "Проверить сертификат",
                        "performer": "Инженер ОТК",
                        "evidence": {"page": 5, "section": "4.1", "quote": "Проверить сертификат"},
                    }
                ],
                "roles": ["Инженер ОТК"],
                "forms": ["Акт входного контроля"],
                "systems": ["1С"],
                "resources": ["Склад"],
                "related_departments": ["ОТК"],
                "owner_candidates": [
                    {
                        "name_or_role": "Начальник ОТК",
                        "reason": "Указан как владелец процесса",
                        "confidence": "high",
                        "evidence": {"page": 3, "quote": "Владелец процесса — Начальник ОТК"},
                    }
                ],
            }
        ],
        "responsibilities": [
            {
                "subject": "Инженер ОТК",
                "responsibility": "Проводит входной контроль",
                "role_type": "performer",
                "confidence": "medium",
                "evidence": {"section": "4.1"},
            }
        ],
        "forms": [
            {
                "name": "Акт входного контроля",
                "code": "Ф-12",
                "purpose": "Фиксация результатов",
                "related_process": "Входной контроль материалов",
            }
        ],
        "related_departments": ["ОТК"],
        "related_documents": ["СТО-45-001"],
        "related_systems": ["1С"],
        "unknowns": [
            {
                "field": "approval_date",
                "reason": "ambiguous",
                "description": "В документе две даты утверждения",
            }
        ],
    }


def test_document_type_confidence_is_parsed() -> None:
    payload = _valid_payload()
    payload["document"]["document_type"] = "policy"
    payload["document"]["document_type_confidence"] = "high"
    result = parse_document_extraction_result(payload)
    assert result.document.document_type == "policy"
    assert result.document.document_type_confidence.value == "high"
    assert not hasattr(result, "document_level")


def test_valid_json_passes() -> None:
    result = parse_document_extraction_result(_valid_payload())

    assert result.document.document_code == "И-15-230"
    assert result.document.scope.departments == ["Отдел качества"]
    assert len(result.processes) == 1
    assert result.processes[0].name == "Входной контроль материалов"
    assert result.participants.developed_by[0].name == "Иванов И.И."
    assert result.unknowns[0].reason.value == "ambiguous"


def test_valid_json_string_passes() -> None:
    payload = json.dumps(_valid_payload(), ensure_ascii=False)
    result = parse_document_extraction_result(payload)
    assert result.document.title == "Инструкция по входному контролю"


def test_empty_arrays_pass() -> None:
    result = parse_document_extraction_result(
        {
            "document": {"document_code": "И-1"},
            "participants": {},
            "processes": [],
            "responsibilities": [],
            "forms": [],
            "related_departments": [],
            "related_documents": [],
            "related_systems": [],
            "unknowns": [],
        }
    )

    assert result.processes == []
    assert result.participants.developed_by == []
    assert result.document.scope.departments == []


def test_missing_optional_fields_use_defaults() -> None:
    result = DocumentExtractionResult.model_validate({"document": {}})

    assert result.document.document_code is None
    assert result.document.scope.text is None
    assert result.document.scope.applies_to_all_company is False
    assert result.participants.checked_by == []
    assert result.related_departments == []


def test_invalid_confidence_is_normalized() -> None:
    payload = _valid_payload()
    payload["processes"][0]["owner_candidates"][0]["confidence"] = "very_high"

    result = parse_document_extraction_result(payload)

    assert result.processes[0].owner_candidates[0].confidence.value == "high"


def test_process_without_name_gets_default() -> None:
    payload = _valid_payload()
    del payload["processes"][0]["name"]

    result = parse_document_extraction_result(payload)

    assert result.processes[0].name == "Процесс"


def test_extra_fields_are_forbidden() -> None:
    payload = _valid_payload()
    payload["unexpected_field"] = "value"

    with pytest.raises(ValidationError) as exc:
        parse_document_extraction_result(payload)

    assert "unexpected_field" in str(exc.value)


def test_nested_extra_fields_are_forbidden() -> None:
    payload = _valid_payload()
    payload["document"]["extra_meta"] = True

    with pytest.raises(ValidationError):
        parse_document_extraction_result(payload)


def test_invalid_role_type_is_normalized() -> None:
    payload = _valid_payload()
    payload["responsibilities"][0]["role_type"] = "manager"

    result = parse_document_extraction_result(payload)

    assert result.responsibilities[0].role_type.value == "unknown"


def test_document_purpose_dict_is_coerced() -> None:
    payload = _valid_payload()
    payload["document"]["purpose"] = {"text": "Обеспечение единообразия управления контрактами"}

    result = parse_document_extraction_result(payload)

    assert result.document.purpose == "Обеспечение единообразия управления контрактами"


def test_llm_like_payload_normalizes_before_validation() -> None:
    payload = {
        "document": {
            "document_code": "TEST",
            "scope": {"positions": None, "departments": "ОТК"},
        },
        "participants": {"approved_by": "директор"},
        "processes": [
            {
                "name": "Процесс",
                "actions": ["Шаг 1", {"action": "Шаг 2"}],
                "forms": [{"name": "Акт", "related_process": ["Процесс"]}],
                "owner_candidates": [{"reason": "не указан", "confidence": "very_high"}],
            }
        ],
        "forms": [{"name": "Форма", "related_process": ["Процесс 1"]}],
        "related_documents": [{"code": "I-1", "title": "Документ"}],
    }

    result = parse_document_extraction_result(payload)

    assert result.document.scope.positions == []
    assert result.document.scope.departments == ["ОТК"]
    assert result.participants.approved_by[0].name == "директор"
    assert result.processes[0].actions[0].action == "Шаг 1"
    assert result.processes[0].owner_candidates[0].name_or_role == "не указан"
    assert result.related_documents == ["I-1"]
