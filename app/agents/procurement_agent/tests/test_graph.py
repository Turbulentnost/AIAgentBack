from __future__ import annotations

import asyncio

from app.agents.procurement_agent.config import AGENT_ID
from app.agents.procurement_agent.graph import NODE_SEQUENCE, build_graph


def _base_state() -> dict:
    return {
        "task_id": "task-1",
        "correlation_id": "corr-1",
        "source_type": "production_material_order",
        "source_1c_ref": "1c://production-order/1",
        "human_role": "production_planner",
        "autonomy_level": 0,
        "requested_operation": "assess_need",
        "idempotency_key": "idem-1",
        "source_data": {
            "nomenclature_ref": "material-1",
            "quantity": 10,
            "requested_date": "2026-08-01",
            "warehouse_ref": "warehouse-1",
            "production_order_ref": "production-1",
        },
        "facts": [],
        "warnings": [],
    }


def test_node_sequence_has_level_zero_steps() -> None:
    assert [name for name, _ in NODE_SEQUENCE] == [
        "validate_request",
        "check_data_quality",
        "prepare_coverage_observation",
        "finalize_result",
    ]


def test_graph_routes_complete_request_to_coverage_check() -> None:
    result = asyncio.run(build_graph().ainvoke(_base_state()))
    assert result["case_status"] == "coverage_check"
    assert result["control_point"] == "KT1"
    assert result["next_agent"] == "procurement_need_supervisor"
    assert result["missing_fields"] == []


def test_graph_stops_when_required_data_is_missing() -> None:
    state = _base_state()
    del state["source_data"]["warehouse_ref"]
    result = asyncio.run(build_graph().ainvoke(state))
    assert result["case_status"] == "data_check"
    assert result["missing_fields"] == ["warehouse_ref"]
    assert "незаполненные" in result["summary"]


def test_graph_rejects_nonzero_autonomy_in_first_increment() -> None:
    state = _base_state()
    state["autonomy_level"] = 1
    result = asyncio.run(build_graph().ainvoke(state))
    assert result["case_status"] == "failed"
    assert "уровень автономности 0" in result["recommendation"]


def test_agent_registered() -> None:
    from app.agents.common.registry import agent_registry
    from app.agents.procurement_agent import service  # noqa: F401

    assert AGENT_ID in agent_registry.list_ids()
