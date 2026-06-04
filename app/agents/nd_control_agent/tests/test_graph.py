from __future__ import annotations
from app.agents.nd_control_agent.config import AGENT_ID
from app.agents.nd_control_agent.graph import NODE_SEQUENCE, build_graph

def test_node_sequence_has_seven_steps() -> None:
    assert [name for name, _ in NODE_SEQUENCE] == ["load_documents", "classify_documents", "check_changes", "check_validity", "check_relations", "assess_confidence", "form_conclusion"]

def test_graph_compiles() -> None:
    assert build_graph() is not None

def test_agent_registered() -> None:
    from app.agents.common.registry import agent_registry
    from app.agents.nd_control_agent import service  # noqa: F401
    assert AGENT_ID in agent_registry.list_ids()
