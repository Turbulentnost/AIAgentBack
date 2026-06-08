from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.agents.tools.executor import ToolExecutionError, ToolExecutor
from app.agents.tools.registry import AgentToolDefinition, register_tool
from app.agents.tools.schemas import ToolContext


class _FakeDb:
    def __init__(self) -> None:
        self.added = []
        self.flushed = False

    def add(self, item) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        self.flushed = True


class _EchoInput(BaseModel):
    value: str


class _EchoOutput(BaseModel):
    echoed: str


async def _echo_tool(payload: _EchoInput, context: ToolContext) -> _EchoOutput:
    return _EchoOutput(echoed=payload.value)


@pytest.mark.asyncio
async def test_tool_executor_validates_allowed_tools_and_logs_call() -> None:
    register_tool(
        AgentToolDefinition(
            name="test_echo_tool",
            description="Test echo tool",
            agent_description="Test echo tool",
            handler=_echo_tool,
            input_model=_EchoInput,
            output_model=_EchoOutput,
        )
    )
    db = _FakeDb()
    context = ToolContext.model_construct(db=db, user=SimpleNamespace(id="user"), agent_id=None, task_id=None)

    result = await ToolExecutor().invoke(
        tool_name="test_echo_tool",
        params={"value": "ok"},
        context=context,
        allowed_tools=["test_echo_tool"],
    )

    assert result == {"echoed": "ok"}
    assert db.flushed is True
    assert db.added[0].tool_name == "test_echo_tool"
    assert db.added[0].success is True


@pytest.mark.asyncio
async def test_tool_executor_rejects_disallowed_tool() -> None:
    context = ToolContext.model_construct(db=_FakeDb(), user=SimpleNamespace(id="user"), agent_id=None, task_id=None)

    with pytest.raises(ToolExecutionError):
        await ToolExecutor().invoke(
            tool_name="test_echo_tool",
            params={"value": "ok"},
            context=context,
            allowed_tools=["another_tool"],
        )
