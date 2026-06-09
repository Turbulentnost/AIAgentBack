from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.builder.capabilities import heuristic_classify_agent_type, is_type_confirmation_message
from app.agents.builder.graph import classify_agent_type
from app.agents.builder.llm import ClassifyAgentTypeLLMResponse
from app.models.enums import AgentBuilderSessionStatus, AgentType


def test_heuristic_classify_consultant():
    goal = "Нужен агент, который отвечает на вопросы по базе знаний"
    assert heuristic_classify_agent_type(goal) == AgentType.CONSULTANT


def test_heuristic_classify_browser_weather_as_consultant():
    goal = "Нужно просмотреть в браузере сайты для погоды и вывести на сегодня"
    assert heuristic_classify_agent_type(goal) == AgentType.CONSULTANT


def test_heuristic_classify_action():
    goal = "Создай задачу в 1С и запланируй совещание"
    assert heuristic_classify_agent_type(goal) == AgentType.ACTION


@pytest.mark.parametrize(
    "message",
    [
        "да",
        "Подтверждаю",
        "Подтверждаю тип Консультант",
        "консультант",
        "согласен",
    ],
)
def test_is_type_confirmation_message_positive(message: str):
    assert is_type_confirmation_message(message) is True


def test_is_type_confirmation_message_negative():
    assert is_type_confirmation_message("нужен поиск в документах") is False


async def test_classify_agent_type_proposes_consultant():
    with patch(
        "app.agents.builder.graph.builder_llm.classify_agent_type",
        new=AsyncMock(
            return_value=ClassifyAgentTypeLLMResponse(
                proposed_agent_type=AgentType.CONSULTANT.value,
                confidence=0.9,
                reasoning="Цель — ответы на вопросы",
                assistant_message="Предлагаю тип «Консультант».",
            )
        ),
    ):
        result = await classify_agent_type(
            {
                "goal": "Отвечать на вопросы сотрудников",
                "collected_requirements": {},
                "conversation": [],
            }
        )

    assert result["requires_user_input"] is True
    assert result["status"] == AgentBuilderSessionStatus.NEEDS_CLARIFICATION.value
    assert result["collected_requirements"]["agent_type_proposal"] == AgentType.CONSULTANT.value


async def test_classify_agent_type_confirms_consultant():
    result = await classify_agent_type(
        {
            "goal": "Отвечать на вопросы сотрудников",
            "user_message": "Подтверждаю тип Консультант",
            "collected_requirements": {
                "agent_type_proposal": AgentType.CONSULTANT.value,
            },
            "conversation": [],
        }
    )

    reqs = result["collected_requirements"]
    assert reqs["agent_type"] == AgentType.CONSULTANT.value
    assert reqs["agent_type_confirmed"] is True
    assert result["requires_user_input"] is False
    assert reqs.get("knowledge_sources_auto") is True
    assert any(item.get("key") == "knowledge_sources" for item in reqs["required_elements"])
    auto = next(item for item in reqs["required_elements"] if item["key"] == "knowledge_sources")
    assert auto.get("auto_resolved") is True


async def test_classify_agent_type_corrects_action_to_consultant_for_browser_goal():
    with patch(
        "app.agents.builder.graph.builder_llm.classify_agent_type",
        new=AsyncMock(
            return_value=ClassifyAgentTypeLLMResponse(
                proposed_agent_type=AgentType.ACTION.value,
                confidence=0.85,
                reasoning="Нужен браузер и извлечение данных",
                assistant_message="Предлагаю тип «Действие».",
            )
        ),
    ):
        result = await classify_agent_type(
            {
                "goal": "Нужно просмотреть в браузере сайты для погоды и вывести на сегодня",
                "collected_requirements": {},
                "conversation": [],
            }
        )

    assert result["collected_requirements"]["agent_type_proposal"] == AgentType.CONSULTANT.value
    assert "Консультант" in result["assistant_messages"][0]


async def test_classify_agent_type_action_not_supported():
    with patch(
        "app.agents.builder.graph.builder_llm.classify_agent_type",
        new=AsyncMock(
            return_value=ClassifyAgentTypeLLMResponse(
                proposed_agent_type=AgentType.ACTION.value,
                confidence=0.8,
                reasoning="Цель — выполнить действие",
                assistant_message="Предлагаю тип «Действие».",
            )
        ),
    ):
        result = await classify_agent_type(
            {
                "goal": "Создать задачу в 1С",
                "collected_requirements": {},
                "conversation": [],
            }
        )

    assert result["requires_user_input"] is True
    assert "Действие" in result["assistant_messages"][0]
    assert result["collected_requirements"]["agent_type_proposal"] == AgentType.ACTION.value
