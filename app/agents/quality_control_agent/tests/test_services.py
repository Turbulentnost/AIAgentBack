"""Service-level HITL tests for quality role agents."""

from __future__ import annotations

import pytest

from app.agents.otk_head_agent.service import OtkHeadService
from app.agents.quality_deputy_director_agent.service import QualityDeputyDirectorService
from app.agents.quality_engineer_agent.service import QualityEngineerService
from app.agents.quality_kpi_agent.service import QualityKpiService
from app.models.enums import ProcurementSourceType


def _base_payload(**extra):
    payload = {
        "task_id": "task-qc-1",
        "case_id": "case-qc-1",
        "correlation_id": "corr-qc-1",
        "source_type": ProcurementSourceType.PRODUCTION_MATERIAL_ORDER.value,
        "source_1c_ref": "1c-ref-1",
        "idempotency_key": "idem-qc-1",
        "source_data": {
            "quality": {
                "item_group": "electronics",
                "present_docs": [],
            }
        },
        "role_context": {"quality_stage": "queued"},
    }
    payload.update(extra)
    return payload


@pytest.mark.asyncio
async def test_otk_head_awaits_engineer_assignment() -> None:
    result = await OtkHeadService().run(_base_payload(), agent_id="otk_head_agent")
    assert result.role_status == "waiting_human"
    assert result.output_data["action"] == "await_presentation"
    assert result.output_data["next_status"] == "quality_queued"


@pytest.mark.asyncio
async def test_otk_head_assigns_engineer() -> None:
    payload = _base_payload()
    payload["source_data"]["quality"]["inspector_id"] = "eng-1"
    payload["source_data"]["quality"]["inspector_name"] = "Иванов"
    result = await OtkHeadService().run(payload, agent_id="otk_head_agent")
    assert result.role_status == "waiting_human"
    assert result.output_data["next_status"] == "quality_assigned"
    assert result.output_data["next_agent"] == "quality_engineer_agent"


@pytest.mark.asyncio
async def test_quality_engineer_doc_check_hitl() -> None:
    payload = _base_payload(role_context={"quality_stage": "assigned"})
    result = await QualityEngineerService().run(payload, agent_id="quality_engineer_agent")
    assert result.role_status == "waiting_human"
    assert result.output_data["stage"] == "doc_check"
    assert result.output_data["mandatory_docs_ok"] is False


@pytest.mark.asyncio
async def test_zdk_disposition_allowlist() -> None:
    payload = _base_payload(
        role_context={"quality_stage": "zdk"},
        source_data={
            "quality": {
                "act_ref": "Ф-10-15/1",
                "scrap_pct": 20,
                "disposition": "запретить",
            }
        },
    )
    result = await QualityDeputyDirectorService().run(
        payload, agent_id="quality_deputy_director_agent"
    )
    assert result.role_status == "waiting_human"
    assert result.output_data["disposition"] == "forbid"
    assert result.output_data["within_allowed_list"] is True


@pytest.mark.asyncio
async def test_kpi_agent_parallel_report() -> None:
    result = await QualityKpiService().run(
        {
            "case_id": "kpi-1",
            "correlation_id": "kpi-1",
            "source_data": {
                "agent_events": [
                    {
                        "agent_id": "otk_head_agent",
                        "role_status": "waiting_human",
                        "checked": True,
                        "output_data": {"actions": ["ASSIGN_ENGINEER"]},
                    }
                ],
                "quality_cases": [
                    {
                        "incoming_control_sla_met": True,
                        "available_without_releasing_status": False,
                        "control_traceability_ok": True,
                    }
                ],
            },
            "role_context": {"agent_ids": ["otk_head_agent"]},
        },
        agent_id="quality_kpi_agent",
    )
    assert result.role_status == "completed"
    assert result.output_data["agents"]
    assert result.output_data["system"]
