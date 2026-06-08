from __future__ import annotations

from app.agents.builder.llm import (
    BlueprintLLMResponse,
    PlanLLMResponse,
    coerce_markdown_fallback,
    parse_plan_markdown,
)


def test_parse_plan_markdown_from_headers():
    text = """### План проектирования агента для погоды
### Сбор требований
Уточнить город и формат ответа
### Подбор инструментов
Выбрать browser tools
### Формирование blueprint
"""
    data = parse_plan_markdown(text)
    assert data is not None
    assert len(data["steps"]) >= 3
    result = coerce_markdown_fallback(PlanLLMResponse, text)
    assert isinstance(result, PlanLLMResponse)
    assert len(result.steps) >= 3


def test_blueprint_llm_response_accepts_dict_workflow_steps():
    payload = {
        "agent_name": "Weather Agent",
        "purpose": "Прогноз погоды",
        "workflow_steps": [
            {"title": "Определение города", "description": "Уточнить город"},
            {"title": "Получение данных", "description": "Запрос к API"},
        ],
        "tools": ["browser_search"],
        "system_prompt": "Ты агент погоды",
        "developer_prompt": "",
    }
    result = BlueprintLLMResponse.model_validate(payload)
    assert len(result.workflow_steps) == 2
    assert "Определение города" in result.workflow_steps[0]


def test_blueprint_llm_response_accepts_dict_human_approval_rules():
    payload = {
        "agent_name": "Weather Agent",
        "purpose": "Прогноз погоды",
        "workflow_steps": ["Получить погоду"],
        "tools": ["fetch_page_via_user_browser"],
        "human_approval_rules": {},
        "test_cases": {"name": "Ростов-на-Дону", "expected": "текстовая погода"},
        "system_prompt": "Ты агент погоды",
        "developer_prompt": "",
    }
    result = BlueprintLLMResponse.model_validate(payload)
    assert result.human_approval_rules == []
    assert result.test_cases == [{"name": "Ростов-на-Дону", "expected": "текстовая погода"}]
