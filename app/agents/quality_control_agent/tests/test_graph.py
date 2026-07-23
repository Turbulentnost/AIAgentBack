"""LangGraph quality pipeline smoke test."""

from __future__ import annotations

import pytest

from app.agents.quality_control_agent.graph import run_quality_pipeline


@pytest.mark.asyncio
async def test_pipeline_parallel_rules_and_route() -> None:
    result = await run_quality_pipeline(
        {
            "case_id": "c1",
            "correlation_id": "r1",
            "source_data": {
                "quality": {
                    "item_group": "cable",
                    "present_docs": ["passport"],
                    "lot_qty": 100,
                }
            },
            "role_context": {"quality_stage": "queued"},
        }
    )
    assert result.get("error") is None
    assert result.get("next_role") == "otk_head_agent"
    assert result.get("sample_rule")
    assert "parallel_results" in result
    assert result["parallel_results"]["rules_version"]
