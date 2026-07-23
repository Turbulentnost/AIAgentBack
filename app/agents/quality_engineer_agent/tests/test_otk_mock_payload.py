"""Validate packaged OTK mock payloads and smoke-run role agents."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.otk_head_agent.service import OtkHeadService
from app.agents.procurement_role_agents.schemas import ProcurementRoleAgentRequest
from app.agents.quality_engineer_agent.service import QualityEngineerService

_DATA = Path(__file__).resolve().parents[1] / "data"
_PAYLOAD_PATH = _DATA / "otk_agent_run_payload.json"
_SCENARIOS_PATH = _DATA / "otk_agent_run_scenarios.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_main_payload_validates_and_has_positions() -> None:
    raw = _load_json(_PAYLOAD_PATH)
    request = ProcurementRoleAgentRequest.model_validate(raw)

    quality = request.source_data["quality"]
    lines = request.source_data["lines"]
    products = request.source_data["products"]
    coverage = request.source_data["coverage"]
    presentation = request.source_data["presentation"]

    assert request.case_id
    assert request.correlation_id
    assert quality["present_docs"]
    assert quality["lot_qty"] == 120
    assert len(lines) == 5
    assert all(line.get("nomenclature_id") and line.get("nomenclature_name") for line in lines)
    assert len(products) >= 5
    assert coverage["products_total"] >= len(products)
    assert coverage["uncovered_orders_count"] >= 1
    assert coverage["orders_total"] == 30
    assert coverage["projects_total"] == 7
    assert len(coverage.get("projects") or []) == 7
    assert presentation.get("project_code")
    assert presentation.get("project_name")
    assert len(presentation["lines"]) == 5
    assert all(line.get("nomenclature") for line in presentation["lines"])


def test_scenarios_file_has_runnable_keys() -> None:
    scenarios = _load_json(_SCENARIOS_PATH)
    expected = {
        "otk_head_queued",
        "otk_head_assign",
        "quality_engineer_doc_check",
        "quality_engineer_inspection",
        "quality_engineer_release",
        "quality_engineer_nc_act",
        "otk_head_confirm_nc",
    }
    assert expected.issubset(scenarios.keys())
    for key in expected:
        payload = scenarios[key]
        assert "case_id" in payload
        assert "source_data" in payload
        ProcurementRoleAgentRequest.model_validate(payload)


@pytest.mark.asyncio
async def test_main_payload_runs_quality_engineer_program() -> None:
    payload = _load_json(_PAYLOAD_PATH)
    result = await QualityEngineerService().run(payload, agent_id="quality_engineer_agent")
    assert result.role_status == "waiting_human"
    assert result.output_data["stage"] == "program"
    assert result.output_data["mandatory_docs_ok"] is True
    assert result.output_data["sample_rule"]["lot_qty"] == 120


@pytest.mark.asyncio
async def test_scenario_otk_head_assign() -> None:
    scenarios = _load_json(_SCENARIOS_PATH)
    result = await OtkHeadService().run(
        scenarios["otk_head_assign"],
        agent_id="otk_head_agent",
    )
    assert result.role_status == "waiting_human"
    assert result.output_data["action"] == "assign_engineer"
    assert result.output_data["next_agent"] == "quality_engineer_agent"


@pytest.mark.asyncio
async def test_scenario_nc_act_handoff() -> None:
    scenarios = _load_json(_SCENARIOS_PATH)
    result = await QualityEngineerService().run(
        scenarios["quality_engineer_nc_act"],
        agent_id="quality_engineer_agent",
    )
    assert result.role_status == "waiting_human"
    assert result.output_data["stage"] == "nc_act"
    assert result.output_data["next_agent"] == "otk_head_agent"
