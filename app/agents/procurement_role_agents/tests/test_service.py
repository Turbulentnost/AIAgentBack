from __future__ import annotations

import pytest

from app.agents import agent_registry
from app.agents.procurement_role_agents.config import (
    AGENT_LABELS,
    OMTO_SUPPORT_MANAGER_AGENT_ID,
    PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
    SOURCE_AGENT_MAP,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_id",
    [
        value
        for value in AGENT_LABELS
        if value
        not in {
            PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
            OMTO_SUPPORT_MANAGER_AGENT_ID,
        }
    ],
)
async def test_role_agent_is_registered_and_waits_for_rules(agent_id: str):
    agent_cls = agent_registry.get(agent_id)
    assert agent_cls is not None

    result = await agent_cls().run(
        {
            "task_id": "task-1",
            "case_id": "case-1",
            "correlation_id": "proc:test:case-1",
            "source_type": next(
                source_type
                for source_type, configured_agent in SOURCE_AGENT_MAP.items()
                if configured_agent == agent_id
            ),
            "source_1c_ref": "ref-1",
            "idempotency_key": "role:case-1:v1",
            "source_data": {"positions": []},
            "role_context": {},
        }
    )

    assert result.agent_id == agent_id
    assert result.role_status == "waiting_external"
    assert result.status == "waiting_external"
    assert result.wait_reason
    assert result.requires_human_review is False


@pytest.mark.asyncio
async def test_engineer_agent_calculates_embedded_confirmed_evidence():
    agent_cls = agent_registry.get(PRODUCTION_PREPARATION_ENGINEER_AGENT_ID)
    assert agent_cls is not None
    result = await agent_cls().run(
        {
            "task_id": "task-1",
            "case_id": "case-1",
            "correlation_id": "proc:test:case-1",
            "source_type": "production_material_order",
            "source_1c_ref": "source-ref",
            "source_number": "НП00-1",
            "idempotency_key": "role:case-1:v1",
            "source_data": {
                "case_number": "НП00-1",
                "source_status": "КВыполнению",
                "required_date": "2026-08-07T00:00:00+00:00",
                "production_order_1c_ref": "production-order",
                "products": [
                    {
                        "line_id": "product-1",
                        "nomenclature_id": "product",
                        "nomenclature_name": "Прибор",
                        "unit": "шт",
                        "product_quantity": "10",
                    }
                ],
                "specifications": [
                    {
                        "specification_id": "spec-1",
                        "name": "РС Прибор",
                        "version": "1",
                        "status": "Действует",
                        "approved": True,
                        "product_id": "product",
                        "materials": [
                            {
                                "line_id": "material-1",
                                "nomenclature_id": "steel",
                                "nomenclature_name": "Сталь",
                                "unit": "кг",
                                "consumption_rate": "5",
                                "production_stage_id": "stage-1",
                            }
                        ],
                    }
                ],
                "supplies": [
                    {
                        "supply_id": "stock",
                        "source_type": "warehouse",
                        "nomenclature_id": "steel",
                        "unit": "кг",
                        "quantity": "50",
                    }
                ],
            },
            "role_context": {"warehouse_1c_ref": "warehouse-main"},
        }
    )
    assert result.role_status == "completed"
    assert result.output_data["positions"][0]["net_requirement"] == "0"


@pytest.mark.asyncio
async def test_omto_agent_data_check_on_missing_fields():
    agent_cls = agent_registry.get(OMTO_SUPPORT_MANAGER_AGENT_ID)
    assert agent_cls is not None
    result = await agent_cls().run(
        {
            "task_id": "task-1",
            "case_id": "case-omto-1",
            "correlation_id": "proc:test:case-omto-1",
            "source_type": "internal_consumption_order",
            "source_1c_ref": "source-ref",
            "idempotency_key": "role:case-omto-1:v1",
            "source_data": {
                "fields": {
                    "cfo": "",
                    "article": "СТ-100",
                    "project": "PRJ-ALPHA",
                    "date": "17.07.2026",
                    "nomenclature": "NOM-КР-12",
                    "quantity": 5,
                }
            },
            "role_context": {},
        }
    )
    assert result.agent_id == OMTO_SUPPORT_MANAGER_AGENT_ID
    assert result.role_status == "waiting_human"
    assert "DATA_CHECK" in result.output_data["actions"]
    assert any(f["field"] == "cfo" for f in result.output_data["findings"])
    for finding in result.output_data["findings"]:
        assert finding["rule_id"]
        assert finding["source_ref"]


@pytest.mark.asyncio
async def test_omto_agent_passes_clean_fields():
    agent_cls = agent_registry.get(OMTO_SUPPORT_MANAGER_AGENT_ID)
    assert agent_cls is not None
    result = await agent_cls().run(
        {
            "task_id": "task-1",
            "case_id": "case-omto-2",
            "correlation_id": "proc:test:case-omto-2",
            "source_type": "internal_consumption_order",
            "source_1c_ref": "source-ref",
            "idempotency_key": "role:case-omto-2:v1",
            "source_data": {
                "fields": {
                    "cfo": "ЦФО-01",
                    "article": "СТ-100",
                    "project": "PRJ-ALPHA",
                    "date": "17.07.2026",
                    "nomenclature": "NOM-КР-12",
                    "quantity": 10,
                }
            },
            "role_context": {},
        }
    )
    assert result.role_status == "completed"
    assert result.output_data["quality_status"] == "ok"
    assert result.output_data["findings"] == []


@pytest.mark.asyncio
async def test_role_agent_rejects_invalid_payload():
    agent_cls = agent_registry.get(next(iter(AGENT_LABELS)))
    assert agent_cls is not None

    result = await agent_cls().run({"case_id": "case-1"})

    assert result.role_status == "failed"
    assert result.status == "failed"
    assert result.output_data["validation_errors"]
