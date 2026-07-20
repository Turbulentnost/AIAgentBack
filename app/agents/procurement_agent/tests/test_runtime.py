from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

from app.agents.procurement_agent.graph import build_graph
from app.agents.procurement_agent.mcp_client import OneCMCPClient
from app.agents.procurement_agent.planner import ProcurementNextAction
from app.agents.procurement_agent.runtime import ProcurementRuntime
from app.agents.procurement_agent.schemas import ProcurementPlanStep
from app.models.procurement import ProcurementCase


class FakeDB:
    async def flush(self) -> None:
        return None

    def add(self, value) -> None:
        return None


class ScriptedPlanner:
    def __init__(self, decisions: list[ProcurementNextAction]) -> None:
        self.decisions = list(decisions)

    async def create_plan(self, **kwargs):
        return (
            "Определить обеспеченность потребности",
            [
                ProcurementPlanStep(
                    step_id="collect",
                    objective="Собрать доказательства из 1С",
                    required_evidence=["need", "supply"],
                )
            ],
            ["need", "supply"],
        )

    async def decide_next(self, **kwargs):
        if not self.decisions:
            return ProcurementNextAction(
                action="human_required",
                short_reason="Нет следующего безопасного действия.",
                human_request=["Проверить план."],
            )
        return self.decisions.pop(0)


class FakeToolExecutor:
    def __init__(self, responses: dict[str, dict] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, *, tool_name, params, context, allowed_tools):
        self.calls.append((tool_name, params))
        response = self.responses.get(tool_name)
        if response is not None:
            return response
        return {
            "status": "success",
            "source_system": "1C_ERP",
            "tool_name": tool_name,
            "object_type": "test",
            "retrieved_at": "2026-07-16T07:00:00+00:00",
            "business_effective_at": "2026-07-16T07:00:00+00:00",
            "freshness_status": "fresh",
            "correlation_id": params["correlation_id"],
            "data": {"items": []},
        }


def _case() -> ProcurementCase:
    return ProcurementCase(
        id=uuid.uuid4(),
        correlation_id=f"corr-{uuid.uuid4()}",
        source_type="production_material_order",
        source_1c_ref="1c://need/1",
        status="new",
        autonomy_level=0,
        requested_operation="assess_need",
        idempotency_key=f"idem-{uuid.uuid4()}",
        case_metadata={},
    )


def _state(runtime: ProcurementRuntime) -> dict:
    return {
        "task_id": str(uuid.uuid4()),
        "correlation_id": runtime.case.correlation_id,
        "case_id": str(runtime.case.id),
        "source_type": "production_material_order",
        "source_1c_ref": "1c://need/1",
        "human_role": "planner",
        "autonomy_level": 0,
        "requested_operation": "assess_need",
        "idempotency_key": runtime.case.idempotency_key,
        "source_data": {
            "positions": [
                {
                    "line_id": "line-1",
                    "nomenclature_id": "item-1",
                    "nomenclature_name": "Материал 1",
                    "unit": "кг",
                    "gross_quantity": "10",
                    "required_date": "2026-07-20T00:00:00+03:00",
                }
            ]
        },
        "facts": [],
        "warnings": [],
        "runtime": runtime,
        "evidence": [],
        "iteration": 0,
        "identical_call_counts": {},
        "successful_call_hashes": {},
    }


def _tool_action(tool_name: str, index: int = 0) -> ProcurementNextAction:
    return ProcurementNextAction(
        action="tool",
        step_id="collect",
        tool_name=tool_name,
        arguments={
            "correlation_id": "corr",
            "nomenclature_ids": ["item-1"],
            "warehouse_ids": [f"warehouse-{index}"],
        },
        short_reason=f"Нужно доказательство {tool_name}.",
    )


def test_integration_agent_plans_calls_tools_and_calculates_coverage() -> None:
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
    responses = {
        name: {
            "status": "success",
            "source_system": "1C_ERP",
            "tool_name": name,
            "object_type": "supply",
            "retrieved_at": "2026-07-16T07:00:00+00:00",
            "business_effective_at": "2026-07-16T07:00:00+00:00",
            "freshness_status": "fresh",
            "correlation_id": "corr",
            "data": {
                "items": (
                    [
                        {
                            "supply_id": "stock-1",
                            "source_type": "warehouse",
                            "nomenclature_id": "item-1",
                            "unit": "кг",
                            "quantity": "10",
                        }
                    ]
                    if name == "onec_get_free_stock"
                    else []
                )
            },
        }
        for name in tools
    }
    events: list[tuple[str, dict]] = []

    async def run():
        case = _case()

        async def write_event(name, payload):
            events.append((name, payload))

        executor = FakeToolExecutor(responses)
        runtime = ProcurementRuntime(
            db=FakeDB(),
            case=case,
            event_writer=write_event,
            planner=ScriptedPlanner(decisions),
            tool_executor=executor,
            task_id=str(uuid.uuid4()),
        )
        result = await build_graph().ainvoke(_state(runtime))
        return result, executor

    result, executor = asyncio.run(run())
    assert result["case_status"] == "closed"
    assert result["coverage_result"]["status"] == "covered"
    assert len(executor.calls) == 6
    event_names = [name for name, _ in events]
    assert "plan_created" in event_names
    assert "coverage_calculated" in event_names
    assert "case_completed" in event_names


def test_successful_same_call_uses_cached_evidence() -> None:
    async def run():
        case = _case()

        async def write_event(name, payload):
            return None

        executor = FakeToolExecutor()
        runtime = ProcurementRuntime(
            db=FakeDB(),
            case=case,
            event_writer=write_event,
            planner=ScriptedPlanner([]),
            tool_executor=executor,
        )
        state = _state(runtime)
        decision = _tool_action("onec_get_free_stock")
        _, args_hash, _ = await runtime.request_tool(state, decision)
        first = await runtime.execute_tool(state, decision, args_hash)
        state["evidence"] = [first.model_dump(mode="json")]
        second = await runtime.execute_tool(state, decision, args_hash)
        return executor, first, second

    executor, first, second = asyncio.run(run())
    assert len(executor.calls) == 1
    assert first.evidence_id == second.evidence_id


def test_capability_unavailable_transitions_to_human() -> None:
    async def run():
        case = _case()

        async def write_event(name, payload):
            return None

        executor = FakeToolExecutor(
            {
                "onec_get_free_stock": {
                    "status": "capability_unavailable",
                    "source_system": "1C_ERP",
                    "tool_name": "onec_get_free_stock",
                    "object_type": "warehouse_stock",
                    "retrieved_at": "2026-07-16T07:00:00+00:00",
                    "freshness_status": "unknown",
                    "correlation_id": "corr",
                    "data": {},
                    "error_code": "capability_unavailable",
                    "error_message": "Недоступно",
                }
            }
        )
        runtime = ProcurementRuntime(
            db=FakeDB(),
            case=case,
            event_writer=write_event,
            planner=ScriptedPlanner([_tool_action("onec_get_free_stock")]),
            tool_executor=executor,
        )
        return await build_graph().ainvoke(_state(runtime))

    result = asyncio.run(run())
    assert result["case_status"] == "human_required"
    assert result["human_action"] is not None


def test_max_iterations_blocks_case() -> None:
    decisions = [
        _tool_action("onec_get_free_stock", 1),
        _tool_action("onec_get_reservations", 2),
    ]

    async def run():
        case = _case()

        async def write_event(name, payload):
            return None

        runtime = ProcurementRuntime(
            db=FakeDB(),
            case=case,
            event_writer=write_event,
            planner=ScriptedPlanner(decisions),
            tool_executor=FakeToolExecutor(),
        )
        with patch(
            "app.agents.procurement_agent.config.MAX_LOOP_ITERATIONS",
            2,
        ):
            return await build_graph().ainvoke(_state(runtime))

    result = asyncio.run(run())
    assert result["case_status"] == "blocked"
    assert "максимальное количество" in result["summary"]


def test_identical_call_limit_blocks_loop() -> None:
    repeated = _tool_action("onec_get_free_stock", 1)
    decisions = [repeated, repeated.model_copy(), repeated.model_copy()]

    async def run():
        case = _case()

        async def write_event(name, payload):
            return None

        runtime = ProcurementRuntime(
            db=FakeDB(),
            case=case,
            event_writer=write_event,
            planner=ScriptedPlanner(decisions),
            tool_executor=FakeToolExecutor(),
        )
        return await build_graph().ainvoke(_state(runtime))

    result = asyncio.run(run())
    assert result["case_status"] == "blocked"
    assert "одинаковых вызовов" in result["summary"]


def test_missing_position_data_transitions_to_human() -> None:
    async def run():
        case = _case()

        async def write_event(name, payload):
            return None

        runtime = ProcurementRuntime(
            db=FakeDB(),
            case=case,
            event_writer=write_event,
            planner=ScriptedPlanner([]),
            tool_executor=FakeToolExecutor(),
        )
        state = _state(runtime)
        state["source_data"] = {"positions": [{"line_id": "line-1"}]}
        return await build_graph().ainvoke(state)

    result = asyncio.run(run())
    assert result["case_status"] == "human_required"


def test_replan_updates_version_and_writes_event() -> None:
    events = []

    async def run():
        case = _case()

        async def write_event(name, payload):
            events.append((name, payload))

        runtime = ProcurementRuntime(
            db=FakeDB(),
            case=case,
            event_writer=write_event,
            planner=ScriptedPlanner([]),
            tool_executor=FakeToolExecutor(),
        )
        plan = await runtime.planning.create_plan(
            goal="goal",
            steps=[ProcurementPlanStep(step_id="old", objective="old")],
            expected_evidence=["old"],
        )
        decision = ProcurementNextAction(
            action="replan",
            short_reason="Нужно другое доказательство.",
            replan_steps=[ProcurementPlanStep(step_id="new", objective="new")],
            expected_evidence=["new"],
        )
        updated = await runtime.replan(decision)
        return plan, updated

    _, updated = asyncio.run(run())
    assert updated.version == 2
    assert updated.steps[0].step_id == "new"
    assert any(name == "plan_replanned" for name, _ in events)


def test_checkpoint_can_be_restored() -> None:
    async def run():
        case = _case()

        async def write_event(name, payload):
            return None

        runtime = ProcurementRuntime(
            db=FakeDB(),
            case=case,
            event_writer=write_event,
            planner=ScriptedPlanner([]),
            tool_executor=FakeToolExecutor(),
        )
        state = _state(runtime)
        state["iteration"] = 3
        await runtime.save_checkpoint(state)
        restored_runtime = ProcurementRuntime(
            db=FakeDB(),
            case=case,
            event_writer=write_event,
            planner=ScriptedPlanner([]),
            tool_executor=FakeToolExecutor(),
        )
        return restored_runtime.restored_checkpoint()

    checkpoint = asyncio.run(run())
    assert checkpoint["iteration"] == 3


def test_mcp_transport_retries_after_timeout() -> None:
    class RetryClient(OneCMCPClient):
        def __init__(self):
            self.max_attempts = 2
            self.timeout_seconds = 1
            self.attempts = 0

        async def _request(self, method, params):
            self.attempts += 1
            if self.attempts == 1:
                raise TimeoutError
            return [{"name": "ok"}]

    client = RetryClient()
    result = asyncio.run(client._with_retry("tools/list", {}))
    assert result == [{"name": "ok"}]
    assert client.attempts == 2
