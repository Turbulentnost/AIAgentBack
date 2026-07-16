from __future__ import annotations

from app.agents.procurement_agent.config import AGENT_ID
from app.agents.procurement_agent.graph import NODE_SEQUENCE, build_graph, validate_request


def test_node_sequence_has_level_zero_steps() -> None:
    assert [name for name, _ in NODE_SEQUENCE] == [
        "validate_request",
        "check_data_quality",
        "load_case_context",
        "ensure_plan",
        "select_next_action",
        "policy_gate",
        "execute_tool",
        "save_observation",
        "evaluate_goal",
        "replan",
        "calculate_coverage_result",
        "require_human",
        "block_case",
        "finalize_result",
    ]


def test_graph_compiles() -> None:
    assert build_graph() is not None


def test_graph_rejects_nonzero_autonomy_in_first_increment() -> None:
    result = validate_request(
        {
            "autonomy_level": 1,
            "requested_operation": "assess_need",
            "warnings": [],
        }
    )
    assert result["case_status"] == "blocked"
    assert "уровень 0" in result["stop_reason"]


def test_agent_registered() -> None:
    from app.agents.common.registry import agent_registry
    from app.agents.procurement_agent import service  # noqa: F401

    assert AGENT_ID in agent_registry.list_ids()
