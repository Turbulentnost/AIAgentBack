from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import func, select

from app.agents.procurement_agent.planner import ProcurementNextAction
from app.agents.procurement_agent.schemas import ProcurementAgentRequest
from app.agents.procurement_agent.service import ProcurementAgent
from app.agents.procurement_agent.tests.test_runtime import (
    FakeToolExecutor,
    ScriptedPlanner,
    _tool_action,
)
from app.db.session import AsyncSessionLocal
from app.models.procurement import ProcurementCaseEvent


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_SMOKE_TESTS") != "1",
    reason="Set RUN_POSTGRES_SMOKE_TESTS=1 to use the configured PostgreSQL with rollback",
)
def test_postgres_events_and_idempotent_replay() -> None:
    async def run() -> None:
        tools = [
            "onec_get_free_stock",
            "onec_get_reservations",
            "onec_get_store_room_stock",
            "onec_get_open_supplier_orders",
            "onec_get_goods_in_transit",
            "onec_get_internal_transfers",
        ]
        decisions = [_tool_action(name, index) for index, name in enumerate(tools)]
        decisions.append(
            ProcurementNextAction(action="complete", short_reason="Доказательств достаточно.")
        )
        executor = FakeToolExecutor()
        unique_key = f"procurement-integration-{uuid.uuid4()}"
        request = ProcurementAgentRequest(
            task_id=str(uuid.uuid4()),
            correlation_id=unique_key,
            source_type="production_material_order",
            source_1c_ref="1c://integration/need",
            human_role="integration_test",
            autonomy_level=0,
            idempotency_key=unique_key,
            source_data={
                "positions": [
                    {
                        "line_id": "line-1",
                        "nomenclature_id": "item-1",
                        "nomenclature_name": "Материал",
                        "unit": "кг",
                        "gross_quantity": "1",
                    }
                ]
            },
        )
        async with AsyncSessionLocal() as db:
            agent = ProcurementAgent()
            first = await agent._run_with_session(
                request,
                db,
                commit=False,
                runtime_options={
                    "planner": ScriptedPlanner(decisions),
                    "tool_executor": executor,
                },
            )
            events_before = await db.scalar(
                select(func.count())
                .select_from(ProcurementCaseEvent)
                .where(ProcurementCaseEvent.correlation_id == unique_key)
            )
            calls_before = len(executor.calls)
            replay = await agent._run_with_session(
                request,
                db,
                commit=False,
                runtime_options={
                    "planner": ScriptedPlanner([]),
                    "tool_executor": executor,
                },
            )
            events_after = await db.scalar(
                select(func.count())
                .select_from(ProcurementCaseEvent)
                .where(ProcurementCaseEvent.correlation_id == unique_key)
            )
            assert first.model_dump() == replay.model_dump()
            assert len(executor.calls) == calls_before
            assert events_after == events_before
            await db.rollback()

    asyncio.run(run())
