from __future__ import annotations

from app.agents.tasks_agent.config import AGENT_ID
from app.agents.tasks_agent.graph import NODE_SEQUENCE, build_graph


def test_node_sequence_has_tasks_steps() -> None:
    assert [name for name, _ in NODE_SEQUENCE] == [
        "validate_input",
        "load_porucheniya",
        "build_tasks_table",
        "summarize_priorities",
        "save_result",
        "wait_user_review",
    ]


def test_graph_compiles() -> None:
    assert build_graph() is not None


def test_agent_registered() -> None:
    from app.agents.common.registry import agent_registry
    from app.agents.tasks_agent import service  # noqa: F401

    assert AGENT_ID in agent_registry.list_ids()
