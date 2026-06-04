from __future__ import annotations

from app.agents.task_compliting_agent.config import AGENT_ID
from app.agents.task_compliting_agent.graph import NODE_SEQUENCE, build_graph, _empty_assessment


def test_node_sequence_has_three_steps() -> None:
    assert [name for name, _ in NODE_SEQUENCE] == [
        "prepare_input",
        "evaluate_comment",
        "form_result",
    ]


def test_graph_compiles() -> None:
    assert build_graph() is not None


def test_empty_assessment_defaults() -> None:
    assessment = _empty_assessment()
    assert assessment.status == "no_answer"
    assert assessment.comment_presence == "empty"


def test_agent_registered() -> None:
    from app.agents.common.registry import agent_registry
    from app.agents.task_compliting_agent import service  # noqa: F401

    assert AGENT_ID in agent_registry.list_ids()
