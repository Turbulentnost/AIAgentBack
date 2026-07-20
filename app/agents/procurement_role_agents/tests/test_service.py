from __future__ import annotations

import pytest

from app.agents import agent_registry
from app.agents.procurement_role_agents.config import AGENT_LABELS, SOURCE_AGENT_MAP


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", list(AGENT_LABELS))
async def test_role_agent_is_registered_and_waits_for_rules(agent_id: str):
    agent_cls = agent_registry.get(agent_id)
    assert agent_cls is not None

    result = await agent_cls().run(
        {
            "task_id": "task-1",
            "case_id": "case-1",
            "correlation_id": "proc:test:case-1",
            "source_type": next(
                source_type
                for source_type, configured_agent in SOURCE_AGENT_MAP.items()
                if configured_agent == agent_id
            ),
            "source_1c_ref": "ref-1",
            "idempotency_key": "role:case-1:v1",
            "source_data": {"positions": []},
            "role_context": {},
        }
    )

    assert result.agent_id == agent_id
    assert result.role_status == "waiting_external"
    assert result.status == "waiting_external"
    assert result.wait_reason
    assert result.requires_human_review is False


@pytest.mark.asyncio
async def test_role_agent_rejects_invalid_payload():
    agent_cls = agent_registry.get(next(iter(AGENT_LABELS)))
    assert agent_cls is not None

    result = await agent_cls().run({"case_id": "case-1"})

    assert result.role_status == "failed"
    assert result.status == "failed"
    assert result.output_data["validation_errors"]
