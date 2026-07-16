from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.procurement_agent import config
from app.agents.procurement_agent.coverage import calculate_coverage
from app.agents.procurement_agent.planner import (
    LLMProcurementPlanner,
    ProcurementNextAction,
    ProcurementPlannerProtocol,
)
from app.agents.procurement_agent.planning import ProcurementPlanningAdapter
from app.agents.procurement_agent.policy import evaluate_procurement_tool
from app.agents.procurement_agent.schemas import (
    ProcurementEvidence,
    ProcurementHumanActionCard,
    ProcurementNeedPosition,
    ProcurementPlan,
    ProcurementSupplyItem,
)
from app.agents.procurement_agent.state import ProcurementCaseState
from app.models.procurement import ProcurementCase
from app.tools.executor import ToolExecutor
from app.tools.schemas import ToolContext


RuntimeEventWriter = Callable[[str, dict[str, Any]], Awaitable[None]]


class ProcurementRuntime:
    def __init__(
        self,
        *,
        db: AsyncSession,
        case: ProcurementCase,
        event_writer: RuntimeEventWriter,
        planner: ProcurementPlannerProtocol | None = None,
        tool_executor: Any | None = None,
        current_user: Any = None,
        task_id: str | None = None,
    ) -> None:
        self.db = db
        self.case = case
        self.write_event = event_writer
        self.planner = planner or LLMProcurementPlanner()
        self.tool_executor = tool_executor or ToolExecutor()
        self.current_user = current_user
        self.task_id = _uuid_or_none(task_id)
        self.planning = ProcurementPlanningAdapter(case, event_writer)

    async def ensure_plan(self, state: ProcurementCaseState) -> ProcurementPlan:
        plan = self.planning.get_active_plan()
        if plan is not None:
            return plan
        goal, steps, expected = await self.planner.create_plan(
            case_id=str(self.case.id),
            correlation_id=state["correlation_id"],
            source_type=state["source_type"],
            source_1c_ref=state["source_1c_ref"],
            source_data=state.get("source_data") or {},
        )
        plan = await self.planning.create_plan(
            goal=goal,
            steps=steps,
            expected_evidence=expected,
        )
        await self.save_checkpoint(state, plan=plan)
        return plan

    async def decide_next(
        self,
        state: ProcurementCaseState,
        plan: ProcurementPlan,
    ) -> ProcurementNextAction:
        evidence = [
            ProcurementEvidence.model_validate(item)
            for item in state.get("evidence") or []
        ]
        return await self.planner.decide_next(
            plan=plan,
            source_data=state.get("source_data") or {},
            evidence=evidence,
            iteration=int(state.get("iteration") or 0),
        )

    async def replan(
        self,
        decision: ProcurementNextAction,
    ) -> ProcurementPlan:
        if not decision.replan_steps:
            raise ValueError("Replan decision must include steps")
        return await self.planning.replan(
            reason=decision.short_reason,
            steps=decision.replan_steps,
            expected_evidence=decision.expected_evidence,
        )

    async def request_tool(
        self,
        state: ProcurementCaseState,
        decision: ProcurementNextAction,
    ) -> tuple[bool, str, str]:
        tool_name = decision.tool_name or ""
        args_hash = _stable_hash(decision.arguments)
        await self.write_event(
            "tool_call_requested",
            {
                "iteration": state.get("iteration", 0),
                "step_id": decision.step_id,
                "tool_name": tool_name,
                "args_hash": args_hash,
                "short_reason": decision.short_reason,
            },
        )
        policy = evaluate_procurement_tool(tool_name, int(state.get("autonomy_level", 0)))
        if not policy.allowed:
            await self.write_event(
                "tool_call_blocked",
                {
                    "iteration": state.get("iteration", 0),
                    "step_id": decision.step_id,
                    "tool_name": tool_name,
                    "args_hash": args_hash,
                    "action_class": policy.action_class.value,
                    "reason": policy.reason,
                },
            )
            return False, args_hash, policy.reason
        await self.write_event(
            "tool_call_allowed",
            {
                "iteration": state.get("iteration", 0),
                "step_id": decision.step_id,
                "tool_name": tool_name,
                "args_hash": args_hash,
                "action_class": policy.action_class.value,
            },
        )
        return True, args_hash, policy.reason

    async def execute_tool(
        self,
        state: ProcurementCaseState,
        decision: ProcurementNextAction,
        args_hash: str,
    ) -> ProcurementEvidence:
        evidence_items = [
            ProcurementEvidence.model_validate(item)
            for item in state.get("evidence") or []
        ]
        cached = next(
            (
                item
                for item in evidence_items
                if item.args_hash == args_hash
                and item.tool_name == decision.tool_name
                and item.status == "success"
                and item.freshness_status == "fresh"
            ),
            None,
        )
        if cached is not None:
            await self.write_event(
                "tool_call_completed",
                {
                    "iteration": state.get("iteration", 0),
                    "tool_name": decision.tool_name,
                    "args_hash": args_hash,
                    "evidence_id": cached.evidence_id,
                    "cached": True,
                },
            )
            return cached

        context = ToolContext.model_construct(
            db=self.db,
            user=self.current_user,
            agent_id=uuid.UUID(config.AGENT_DB_ID),
            task_id=self.task_id,
            allow_open_web=False,
        )
        try:
            response = await self.tool_executor.invoke(
                tool_name=decision.tool_name or "",
                params=decision.arguments,
                context=context,
                allowed_tools=config.READ_ONLY_TOOL_NAMES,
            )
            evidence = _evidence_from_response(
                response=response,
                tool_name=decision.tool_name or "",
                args_hash=args_hash,
                correlation_id=state["correlation_id"],
            )
            await self.write_event(
                "tool_call_completed",
                {
                    "iteration": state.get("iteration", 0),
                    "tool_name": decision.tool_name,
                    "args_hash": args_hash,
                    "evidence_id": evidence.evidence_id,
                    "status": evidence.status,
                    "freshness_status": evidence.freshness_status,
                    "cached": False,
                },
            )
            return evidence
        except Exception as exc:
            await self.write_event(
                "tool_call_failed",
                {
                    "iteration": state.get("iteration", 0),
                    "tool_name": decision.tool_name,
                    "args_hash": args_hash,
                    "error_type": type(exc).__name__,
                },
            )
            raise

    async def add_evidence(
        self,
        evidence: ProcurementEvidence,
        step_id: str | None,
    ) -> None:
        await self.planning.add_evidence(evidence, step_id=step_id)

    async def calculate_coverage(self, state: ProcurementCaseState):
        evidence = [
            ProcurementEvidence.model_validate(item)
            for item in state.get("evidence") or []
        ]
        source_data = state.get("source_data") or {}
        positions, position_issues = _extract_positions(source_data, evidence)
        supplies, supply_issues = _extract_supplies(evidence)
        capability_issues = _mandatory_capability_issues(source_data, evidence)
        freshness_issues = [
            f"Доказательство {item.evidence_id} не имеет подтверждённой актуальности."
            for item in evidence
            if item.status == "success" and item.freshness_status != "fresh"
        ]
        failed_issues = [
            f"Возможность {item.tool_name} недоступна."
            for item in evidence
            if item.status == "capability_unavailable"
        ]
        result = calculate_coverage(
            case_id=str(self.case.id),
            source_basis={
                "source_type": state["source_type"],
                "source_1c_ref": state["source_1c_ref"],
                "correlation_id": state["correlation_id"],
            },
            positions=positions,
            supplies=supplies,
            evidence_ids=[item.evidence_id for item in evidence],
            data_issues=list(
                dict.fromkeys(
                    [
                        *position_issues,
                        *supply_issues,
                        *capability_issues,
                        *freshness_issues,
                        *failed_issues,
                    ]
                )
            ),
        )
        await self.write_event(
            "coverage_calculated",
            {
                "status": result.status,
                "positions_count": len(result.positions),
                "critical_positions": result.critical_positions,
                "missing_data": result.missing_data,
                "evidence_ids": result.evidence_ids,
            },
        )
        return result

    async def save_checkpoint(
        self,
        state: ProcurementCaseState,
        *,
        plan: ProcurementPlan | None = None,
    ) -> None:
        metadata = dict(self.case.case_metadata or {})
        metadata["checkpoint"] = {
            "graph_version": config.GRAPH_VERSION,
            "iteration": state.get("iteration", 0),
            "plan": (plan.model_dump(mode="json") if plan else state.get("plan")),
            "evidence": state.get("evidence") or metadata.get("evidence") or [],
            "identical_call_counts": state.get("identical_call_counts") or {},
            "successful_call_hashes": state.get("successful_call_hashes") or {},
            "case_status": state.get("case_status"),
            "control_point": state.get("control_point"),
        }
        self.case.case_metadata = metadata
        await self.db.flush()

    def restored_checkpoint(self) -> dict[str, Any]:
        return dict((self.case.case_metadata or {}).get("checkpoint") or {})


def _evidence_from_response(
    *,
    response: Any,
    tool_name: str,
    args_hash: str,
    correlation_id: str,
) -> ProcurementEvidence:
    if not isinstance(response, dict):
        raise ValueError("Tool response must be an object")
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    content_hash = _stable_hash(data)
    return ProcurementEvidence(
        evidence_id=str(uuid.uuid4()),
        source_system=str(response.get("source_system") or "1C_ERP"),
        tool_name=tool_name,
        object_type=str(response.get("object_type") or "onec_business_data"),
        object_id=response.get("object_id"),
        row_ids=[str(value) for value in response.get("row_ids") or []],
        retrieved_at=response.get("retrieved_at") or datetime.now(UTC),
        business_effective_at=response.get("business_effective_at"),
        data=data,
        freshness_status=response.get("freshness_status") or "unknown",
        correlation_id=correlation_id,
        args_hash=args_hash,
        content_hash=content_hash,
        status=response.get("status") or "failed",
        error_code=response.get("error_code"),
        error_message=response.get("error_message"),
    )


def _extract_positions(
    source_data: dict[str, Any],
    evidence: list[ProcurementEvidence],
) -> tuple[list[ProcurementNeedPosition], list[str]]:
    raw_positions = source_data.get("positions")
    if not isinstance(raw_positions, list):
        need_evidence = next(
            (
                item
                for item in evidence
                if item.tool_name == "onec_get_procurement_need_lines" and item.status == "success"
            ),
            None,
        )
        raw_positions = (need_evidence.data.get("positions") if need_evidence else None)
    if not isinstance(raw_positions, list) and source_data.get("nomenclature_ref"):
        raw_positions = [
            {
                "line_id": str(source_data.get("line_id") or "source-line-1"),
                "nomenclature_id": source_data.get("nomenclature_ref"),
                "nomenclature_name": source_data.get("nomenclature_name")
                or str(source_data.get("nomenclature_ref")),
                "unit": source_data.get("unit"),
                "gross_quantity": source_data.get("quantity"),
                "required_date": source_data.get("requested_date"),
                "calculation_source": "direct_material_quantity",
            }
        ]
    positions: list[ProcurementNeedPosition] = []
    issues: list[str] = []
    for index, raw in enumerate(raw_positions or []):
        try:
            positions.append(ProcurementNeedPosition.model_validate(raw))
        except ValueError:
            issues.append(f"Некорректный формат строки потребности {index + 1}.")
    if not positions:
        issues.append("Не получены строки исходной потребности.")
    return positions, issues


def _extract_supplies(
    evidence: list[ProcurementEvidence],
) -> tuple[list[ProcurementSupplyItem], list[str]]:
    supplies: list[ProcurementSupplyItem] = []
    issues: list[str] = []
    for item in evidence:
        if item.status != "success" or item.tool_name == "onec_get_procurement_need_lines":
            continue
        raw_items = item.data.get("items")
        if not isinstance(raw_items, list):
            issues.append(
                f"Доказательство {item.evidence_id} "
                "не содержит типизированный список items."
            )
            continue
        for index, raw in enumerate(raw_items):
            candidate = dict(raw) if isinstance(raw, dict) else {}
            candidate.setdefault("evidence_id", item.evidence_id)
            try:
                supplies.append(ProcurementSupplyItem.model_validate(candidate))
            except ValueError:
                issues.append(
                    f"Некорректная запись обеспечения {index + 1} "
                    f"в доказательстве {item.evidence_id}."
                )
    return supplies, issues


def _mandatory_capability_issues(
    source_data: dict[str, Any],
    evidence: list[ProcurementEvidence],
) -> list[str]:
    successful_tools = {item.tool_name for item in evidence if item.status == "success"}
    required = {
        "onec_get_free_stock",
        "onec_get_reservations",
        "onec_get_store_room_stock",
        "onec_get_open_supplier_orders",
        "onec_get_goods_in_transit",
        "onec_get_internal_transfers",
    }
    if (
        not isinstance(source_data.get("positions"), list)
        and not source_data.get("nomenclature_ref")
    ):
        required.add("onec_get_procurement_need_lines")
    return [
        f"Отсутствует обязательное доказательство от {tool_name}."
        for tool_name in sorted(required - successful_tools)
    ]


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


__all__ = ["ProcurementRuntime"]
