from __future__ import annotations

from app.agents.builder.templates.consultant import (
    CONSULTANT_CONFIDENCE_THRESHOLD,
    build_auto_knowledge_sources_element,
    element_needs_user_input,
    filter_consultant_questions,
    init_consultant_requirements,
    resolve_knowledge_sources_from_tools,
    sanitize_consultant_elements,
)
from app.agents.builder.validators import validate_required_elements


def test_resolve_knowledge_sources_from_tools():
    catalog = [
        {"name": "get_current_date", "description": "Текущая дата", "implemented": True},
        {"name": "search_knowledge_base", "description": "Поиск в БЗ", "implemented": True},
        {"name": "fetch_page_via_user_browser", "description": "Браузер", "implemented": True},
        {"name": "other_tool", "description": "Другое", "implemented": False},
    ]
    sources = resolve_knowledge_sources_from_tools(catalog, "погода на сегодня")
    assert "get_current_date" in sources["recommended_tools"]
    assert "fetch_page_via_user_browser" in sources["recommended_tools"]
    assert sources["knowledge_sources_auto"] is True


def test_init_consultant_requirements_auto_sources():
    reqs = init_consultant_requirements(
        {"goal": "Консультант по HR"},
        [{"name": "search_knowledge_base", "description": "Поиск", "implemented": True}],
    )
    elements = reqs["required_elements"]
    auto = next(item for item in elements if item["key"] == "knowledge_sources")
    assert auto["status"] == "filled"
    assert auto["auto_resolved"] is True
    assert reqs["knowledge_sources_auto"] is True


def test_element_needs_user_input_respects_confidence():
    high_conf = {
        "key": "response_format",
        "label": "Формат",
        "required": True,
        "status": "pending",
        "confidence": CONSULTANT_CONFIDENCE_THRESHOLD,
    }
    low_conf = {
        "key": "location",
        "label": "Город",
        "question": "Для какого города?",
        "required": True,
        "status": "pending",
        "confidence": 0.5,
    }
    assert element_needs_user_input(high_conf) is False
    assert element_needs_user_input(low_conf) is True
    assert element_needs_user_input(build_auto_knowledge_sources_element("БЗ")) is False


def test_filter_consultant_questions_blocks_sites_and_format():
    questions = [
        "Для какого города нужна погода?",
        "Какие конкретно погодные сайты использовать?",
        "В каком формате предоставить результат?",
    ]
    filtered = filter_consultant_questions(questions)
    assert filtered == ["Для какого города нужна погода?"]


def test_sanitize_consultant_elements_drops_forbidden_pending():
    elements = sanitize_consultant_elements(
        [
            build_auto_knowledge_sources_element("tools"),
            {
                "key": "weather_sites",
                "label": "Сайты",
                "question": "Какие сайты?",
                "status": "pending",
            },
            {
                "key": "location",
                "label": "Город",
                "question": "Какой город?",
                "status": "pending",
                "required": True,
            },
        ]
    )
    keys = [item["key"] for item in elements]
    assert "weather_sites" not in keys
    assert "location" in keys


def test_validate_required_elements_only_dynamic_pending():
    validation = validate_required_elements(
        {
            "required_elements": [
                build_auto_knowledge_sources_element("search_knowledge_base: Поиск"),
                {
                    "key": "response_format",
                    "label": "Формат",
                    "required": True,
                    "value": "текст",
                    "status": "filled",
                    "confidence": 0.95,
                },
            ]
        }
    )
    assert validation["valid"] is True
    assert validation["missing"] == []
