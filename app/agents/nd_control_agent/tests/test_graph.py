from __future__ import annotations
from app.agents.nd_control_agent.config import AGENT_ID
from app.agents.nd_control_agent.graph import NODE_SEQUENCE, build_graph

def test_node_sequence_has_nd_change_steps() -> None:
    assert [name for name, _ in NODE_SEQUENCE] == [
        "validate_input",
        "detect_target_document",
        "calculate_document_confidence",
        "require_user_document_selection",
        "locate_change_place",
        "classify_change_operation",
        "extract_current_text",
        "apply_change_to_draft",
        "generate_diff",
        "analyze_related_documents",
        "generate_change_notice",
        "prepare_approval_route",
        "save_result",
        "wait_user_review",
        "send_to_approval",
    ]

def test_graph_compiles() -> None:
    assert build_graph() is not None

def test_agent_registered() -> None:
    from app.agents.common.registry import agent_registry
    from app.agents.nd_control_agent import service  # noqa: F401
    assert AGENT_ID in agent_registry.list_ids()
