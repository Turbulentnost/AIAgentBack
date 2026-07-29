from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents.procurement_manager_agent.material_bank import reset_material_bank_for_tests
from app.agents.procurement_pipeline.graph import build_graph, procurement_pipeline_graph
from app.agents.procurement_role_agents.config import (
    OTK_HEAD_AGENT_ID,
    PROCUREMENT_LOGISTICS_AGENT_ID,
    PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
)
from app.models.enums import ProcurementSourceType


STEEL_POSITIONS = [
    {
        "line_id": "line-steel",
        "nomenclature_id": "steel",
        "nomenclature_name": "Сталь",
        "quantity": "200",
        "unit": "кг",
    }
]


def test_studio_export_compiles_without_checkpointer() -> None:
    assert procurement_pipeline_graph is not None
    assert build_graph() is not None


@pytest.mark.asyncio
async def test_pipeline_happy_path_auto_approve() -> None:
    reset_material_bank_for_tests()
    result = await build_graph().ainvoke(
        {
            "case_id": "pipe-1",
            "case_number": "REQ-PIPE-1",
            "source_type": ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value,
            "positions": STEEL_POSITIONS,
            "auto_approve": True,
        }
    )
    assert result["picker_agent"] == PRODUCTION_PREPARATION_ENGINEER_AGENT_ID
    assert result["coverage_status"] == "deficit"
    assert result["recommendation"]
    assert result["purchase_order_draft"]
    assert result["approval"]["action"] == "approve_order_draft"
    assert result["current_agent"] == OTK_HEAD_AGENT_ID
    assert result["case_status"] == "quality_queued"
    assert result["status"] == "completed"
    assert result["stage"] == "finalize"


@pytest.mark.asyncio
async def test_pipeline_interrupt_and_resume_reject() -> None:
    reset_material_bank_for_tests()
    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "procurement-pipeline-reject"}}
    paused = await graph.ainvoke(
        {
            "case_id": "pipe-2",
            "case_number": "REQ-PIPE-2",
            "source_type": ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value,
            "positions": STEEL_POSITIONS,
        },
        config=config,
    )
    assert paused["__interrupt__"]
    interrupt = paused["__interrupt__"][0]
    payload = getattr(interrupt, "value", interrupt)
    assert payload["type"] == "procurement_pipeline_approval"
    assert PROCUREMENT_LOGISTICS_AGENT_ID in {
        paused.get("current_agent"),
        paused.get("next_agent"),
    }

    finished = await graph.ainvoke(
        Command(resume={"action": "reject"}),
        config=config,
    )
    assert finished["status"] == "completed_with_issues"
    assert finished["requires_human"] is True
    assert finished.get("case_status") == "human_required"


@pytest.mark.asyncio
async def test_pipeline_empty_positions_fails_cleanly() -> None:
    result = await build_graph().ainvoke(
        {
            "case_id": "pipe-empty",
            "positions": [],
            "auto_approve": True,
        }
    )
    assert result["status"] == "failed"
    assert result["stage"] == "finalize"
    assert "позиц" in (result.get("summary") or "").lower() or result.get("stop_reason")
