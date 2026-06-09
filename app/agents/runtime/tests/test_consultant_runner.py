from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.runtime import consultant_runner as runner_module
from app.agents.runtime.consultant_runner import ConsultantRunner


def _chat_response(*, content: str | None = None, tool_calls: list | None = None) -> dict:
    message: dict = {"content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


@pytest.mark.asyncio
async def test_runner_tool_calling_loop_records_steps():
    blueprint = {
        "tools": ["get_current_date"],
        "prompts": {"system": "Ты консультант."},
        "agent_card": {"purpose": "тест"},
    }
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "get_current_date", "arguments": "{\"timezone\": \"Europe/Moscow\"}"},
    }
    chat_mock = AsyncMock(
        side_effect=[
            _chat_response(tool_calls=[tool_call]),
            _chat_response(content="Сегодня хорошая погода."),
        ]
    )
    executor_mock = AsyncMock(return_value={"date_ru": "9 июня 2026", "date_iso": "2026-06-09"})

    runner = ConsultantRunner()
    with (
        patch.object(runner_module.llm_gateway, "chat", new=chat_mock),
        patch.object(runner._executor, "invoke", new=executor_mock),
    ):
        result = await runner.execute(
            blueprint=blueprint,
            test_query="Какая погода сегодня?",
            db=object(),
            user=object(),
        )

    assert result.final_answer == "Сегодня хорошая погода."
    assert result.used_fallback is False
    assert len(result.steps) == 1
    assert result.steps[0]["tool_name"] == "get_current_date"
    assert result.steps[0]["status"] == "completed"
    assert result.stats["total_steps"] == 1
    assert result.stats["success_steps"] == 1
    node_ids = {node["id"] for node in result.executed_graph["nodes"]}
    assert {"start", "end", "step_1"}.issubset(node_ids)


@pytest.mark.asyncio
async def test_runner_deterministic_fallback_when_no_tool_calls():
    blueprint = {
        "tools": ["get_current_date"],
        "prompts": {"system": "Ты консультант."},
        "agent_card": {"purpose": "тест"},
    }
    # First chat call returns no tool calls and no content -> triggers fallback,
    # then the fallback formatting call returns the final answer.
    chat_mock = AsyncMock(
        side_effect=[
            _chat_response(content=""),
            _chat_response(content="Финальный ответ по данным."),
        ]
    )
    executor_mock = AsyncMock(return_value={"date_ru": "9 июня 2026"})

    runner = ConsultantRunner()
    with (
        patch.object(runner_module.llm_gateway, "chat", new=chat_mock),
        patch.object(runner._executor, "invoke", new=executor_mock),
    ):
        result = await runner.execute(
            blueprint=blueprint,
            test_query="Какая погода сегодня?",
            db=object(),
            user=object(),
        )

    assert result.used_fallback is True
    assert result.final_answer == "Финальный ответ по данным."
    assert any(step["tool_name"] == "get_current_date" for step in result.steps)


@pytest.mark.asyncio
async def test_runner_step_callbacks_invoked():
    blueprint = {"tools": ["get_current_date"], "prompts": {}, "agent_card": {}}
    tool_call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "get_current_date", "arguments": "{}"},
    }
    chat_mock = AsyncMock(
        side_effect=[
            _chat_response(tool_calls=[tool_call]),
            _chat_response(content="Готово."),
        ]
    )
    executor_mock = AsyncMock(return_value={"date_iso": "2026-06-09"})

    started: list[dict] = []
    finished: list[dict] = []

    async def on_start(info):
        started.append(info)
        return len(started)

    async def on_finish(handle, record):
        finished.append(record)

    runner = ConsultantRunner()
    with (
        patch.object(runner_module.llm_gateway, "chat", new=chat_mock),
        patch.object(runner._executor, "invoke", new=executor_mock),
    ):
        await runner.execute(
            blueprint=blueprint,
            test_query="дата",
            db=object(),
            user=object(),
            on_step_start=on_start,
            on_step_finish=on_finish,
        )

    assert len(started) == 1
    assert len(finished) == 1
    assert finished[0]["status"] == "completed"
