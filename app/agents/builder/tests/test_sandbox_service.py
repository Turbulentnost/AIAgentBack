from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent_builder_service import AgentBuilderService, AgentBuilderServiceError


class _FakeDB:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_start_sandbox_run_enqueues_and_returns_run():
    service = AgentBuilderService(_FakeDB())
    session = SimpleNamespace(
        id=uuid.uuid4(),
        goal="Погода в Ростове",
        proposed_agent_structure={"tools": ["get_current_date"]},
    )
    fake_run = SimpleNamespace(id=uuid.uuid4(), steps=[])
    current_user = SimpleNamespace(id=uuid.uuid4())

    with (
        patch.object(AgentBuilderService, "get_session", new=AsyncMock(return_value=session)),
        patch.object(AgentBuilderService, "_load_sandbox_run", new=AsyncMock(return_value=fake_run)),
        patch("app.workers.tasks.run_sandbox") as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        run = await service.start_sandbox_run(
            session.id,
            test_query="Какая погода сегодня?",
            current_user=current_user,
        )

    assert run is fake_run
    mock_task.apply_async.assert_called_once()
    assert service.db.added, "Sandbox run row must be added to the session"
    created = service.db.added[0]
    assert created.status == "pending"
    assert created.test_query == "Какая погода сегодня?"


@pytest.mark.asyncio
async def test_start_sandbox_run_requires_blueprint():
    service = AgentBuilderService(_FakeDB())
    session = SimpleNamespace(id=uuid.uuid4(), goal="g", proposed_agent_structure=None)
    current_user = SimpleNamespace(id=uuid.uuid4())

    with patch.object(AgentBuilderService, "get_session", new=AsyncMock(return_value=session)):
        with pytest.raises(AgentBuilderServiceError):
            await service.start_sandbox_run(session.id, test_query="q", current_user=current_user)
