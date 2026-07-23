from __future__ import annotations

import pytest

from app.agents import agent_registry
from app.agents.procurement_role_agents.config import (
    AGENT_LABELS,
    DEPARTMENT_INITIATOR_AGENT_ID,
    OMTO_CHIEF_AGENT_ID,
    PRODUCTION_DISPATCHER_AGENT_ID,
    PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
    SOURCE_AGENT_MAP,
    WAREHOUSE_MANAGER_AGENT_ID,
    WAREHOUSE_PICKER_AGENT_ID,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_id",
    [
        DEPARTMENT_INITIATOR_AGENT_ID,
        WAREHOUSE_MANAGER_AGENT_ID,
        OMTO_CHIEF_AGENT_ID,
    ],
)
async def test_role_agent_is_registered_and_waits_for_rules(agent_id: str):
    agent_cls = agent_registry.get(agent_id)
    assert agent_cls is not None
    source_type = next(
        (
            mapped_source
            for mapped_source, configured_agent in SOURCE_AGENT_MAP.items()
            if configured_agent == agent_id
        ),
        "production_material_order",
    )

    result = await agent_cls().run(
        {
            "task_id": "task-1",
            "case_id": "case-1",
            "correlation_id": "proc:test:case-1",
            "source_type": source_type,
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
@pytest.mark.parametrize(
    ("stock_quantity", "expected_status", "expected_net"),
    [
        ("50", "completed", "0"),
        ("40", "waiting_human", "10"),
    ],
)
async def test_engineer_agent_calculates_embedded_confirmed_evidence(
    stock_quantity: str,
    expected_status: str,
    expected_net: str,
):
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
                        "quantity": stock_quantity,
                    }
                ],
            },
            "role_context": {"warehouse_1c_ref": "warehouse-main"},
        }
    )
    assert result.role_status == expected_status
    assert result.output_data["positions"][0]["net_requirement"] == expected_net
    if expected_status == "waiting_human":
        assert result.output_data["decision_kind"] == "purchase_confirmation"


@pytest.mark.asyncio
async def test_dispatcher_agent_calculates_embedded_supply():
    agent_cls = agent_registry.get(PRODUCTION_DISPATCHER_AGENT_ID)
    assert agent_cls is not None
    result = await agent_cls().run(
        {
            "task_id": "task-1",
            "case_id": "case-1",
            "correlation_id": "proc:test:case-1",
            "source_type": "reorder_point",
            "source_1c_ref": "source-ref",
            "source_number": "ТЗ-1",
            "idempotency_key": "role:case-1:v1",
            "source_data": {
                "case_number": "ТЗ-1",
                "skip_external": True,
                "stock_growth_coefficient": "1",
                "positions": [
                    {
                        "line_id": "1",
                        "nomenclature_id": "mat-1",
                        "nomenclature_name": "Материал",
                        "unit": "шт",
                        "quantity": "30",
                        "minimum_stock": "10",
                        "maximum_stock": "30",
                    }
                ],
                "supplies": [
                    {
                        "supply_id": "stock",
                        "source_type": "warehouse",
                        "nomenclature_id": "mat-1",
                        "unit": "шт",
                        "quantity": "5",
                        "warehouse_id": "wh-main",
                    }
                ],
            },
            "role_context": {"warehouse_1c_ref": "wh-main"},
        }
    )
    assert result.role_status == "waiting_human"
    assert result.output_data["decision_kind"] == "supply_confirmation"
    assert result.output_data["positions"][0]["below_minimum"] is True


@pytest.mark.asyncio
async def test_role_agent_rejects_invalid_payload():
    agent_cls = agent_registry.get(next(iter(AGENT_LABELS)))
    assert agent_cls is not None

    result = await agent_cls().run({"case_id": "case-1"})

    assert result.role_status == "failed"
    assert result.status == "failed"
    assert result.output_data["validation_errors"]
