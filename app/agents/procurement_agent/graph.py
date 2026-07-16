from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.procurement_agent import config
from app.agents.procurement_agent.planner import PlannerUnavailableError, ProcurementNextAction
from app.agents.procurement_agent.policy import evaluate_procurement_action
from app.agents.procurement_agent.schemas import (
    ProcurementEvidence,
    ProcurementHumanActionCard,
    ProcurementPlan,
)
from app.agents.procurement_agent.state import ProcurementCaseState
from app.models.enums import ProcurementCaseStatus


def validate_request(state: ProcurementCaseState) -> dict[str, Any]:
    autonomy_level = int(state.get("autonomy_level", 0))
    policy = evaluate_procurement_action(
        state.get("requested_operation") or "assess_need",
        autonomy_level,
    )
    result: dict[str, Any] = {
        "case_status": ProcurementCaseStatus.DATA_CHECK.value,
        "control_point": "KT1",
        "action_class": policy.action_class.value,
        "warnings": list(state.get("warnings") or []),
    }
    if autonomy_level != config.SUPPORTED_AUTONOMY_LEVEL:
        result.update(
            case_status=ProcurementCaseStatus.BLOCKED.value,
            stop_reason=(
                "Версия графа поддерживает только уровень "
                f"{config.SUPPORTED_AUTONOMY_LEVEL}."
            ),
        )
    elif not policy.allowed:
        result.update(
            case_status=(
                ProcurementCaseStatus.HUMAN_REQUIRED.value
                if policy.requires_human
                else ProcurementCaseStatus.BLOCKED.value
            ),
            stop_reason=policy.reason,
        )
    return result


def check_data_quality(state: ProcurementCaseState) -> dict[str, Any]:
    source_data = state.get("source_data") or {}
    positions = source_data.get("positions")
    missing_fields: list[str] = []
    if isinstance(positions, list):
        for index, position in enumerate(positions):
            if not isinstance(position, dict):
                missing_fields.append(f"positions[{index}]")
                continue
            for field in ("line_id", "nomenclature_name", "unit"):
                if position.get(field) in (None, ""):
                    missing_fields.append(f"positions[{index}].{field}")
            has_direct = position.get("gross_quantity") is not None
            has_norm = (
                position.get("product_quantity") is not None
                and position.get("consumption_rate") is not None
            )
            if not has_direct and not has_norm:
                missing_fields.append(f"positions[{index}].quantity_or_norm")
    if missing_fields:
        return {
            "missing_fields": missing_fields,
            "case_status": ProcurementCaseStatus.HUMAN_REQUIRED.value,
            "stop_reason": "В переданных строках потребности отсутствуют обязательные реквизиты.",
        }
    return {"missing_fields": []}


async def load_case_context(state: ProcurementCaseState) -> dict[str, Any]:
    runtime = state["runtime"]
    checkpoint = runtime.restored_checkpoint()
    evidence = (
        checkpoint.get("evidence")
        or (runtime.case.case_metadata or {}).get("evidence")
        or []
    )
    return {
        "case_status": ProcurementCaseStatus.COVERAGE_CHECK.value,
        "iteration": int(checkpoint.get("iteration") or 0),
        "plan": checkpoint.get("plan"),
        "evidence": evidence,
        "identical_call_counts": checkpoint.get("identical_call_counts") or {},
        "successful_call_hashes": checkpoint.get("successful_call_hashes") or {},
    }


async def ensure_plan(state: ProcurementCaseState) -> dict[str, Any]:
    try:
        plan = await state["runtime"].ensure_plan(state)
        return {"plan": plan.model_dump(mode="json")}
    except Exception as exc:
        return {
            "case_status": ProcurementCaseStatus.HUMAN_REQUIRED.value,
            "stop_reason": f"Не удалось сформировать план: {type(exc).__name__}.",
        }


async def select_next_action(state: ProcurementCaseState) -> dict[str, Any]:
    try:
        plan = ProcurementPlan.model_validate(state["plan"])
        decision = await state["runtime"].decide_next(state, plan)
        await state["runtime"].write_event(
            "agent_action_selected",
            {
                "iteration": state.get("iteration", 0),
                "action": decision.action,
                "step_id": decision.step_id,
                "tool_name": decision.tool_name,
                "short_reason": decision.short_reason,
            },
        )
        return {"next_action": decision.model_dump(mode="json")}
    except (PlannerUnavailableError, ValueError, KeyError) as exc:
        return {
            "case_status": ProcurementCaseStatus.HUMAN_REQUIRED.value,
            "stop_reason": (
                "Планировщик не может выбрать безопасное действие: "
                f"{type(exc).__name__}."
            ),
        }


async def policy_gate(state: ProcurementCaseState) -> dict[str, Any]:
    decision = ProcurementNextAction.model_validate(state["next_action"])
    allowed, args_hash, reason = await state["runtime"].request_tool(state, decision)
    if not allowed:
        return {
            "case_status": ProcurementCaseStatus.BLOCKED.value,
            "stop_reason": reason,
            "current_tool_call": None,
        }
    call_key = f"{decision.tool_name}:{args_hash}"
    counts = dict(state.get("identical_call_counts") or {})
    counts[call_key] = counts.get(call_key, 0) + 1
    if counts[call_key] > config.MAX_IDENTICAL_TOOL_CALLS:
        await state["runtime"].write_event(
            "tool_call_blocked",
            {
                "tool_name": decision.tool_name,
                "args_hash": args_hash,
                "reason": "identical_call_limit",
                "count": counts[call_key],
            },
        )
        return {
            "identical_call_counts": counts,
            "case_status": ProcurementCaseStatus.BLOCKED.value,
            "stop_reason": "Превышено допустимое количество одинаковых вызовов.",
        }
    if decision.step_id:
        try:
            await state["runtime"].planning.update_step(
                step_id=decision.step_id,
                status="running",
            )
        except (KeyError, RuntimeError):
            return {
                "case_status": ProcurementCaseStatus.HUMAN_REQUIRED.value,
                "stop_reason": "Выбранный шаг отсутствует в активном плане.",
            }
    return {
        "identical_call_counts": counts,
        "current_tool_call": {
            "tool_name": decision.tool_name,
            "arguments": decision.arguments,
            "args_hash": args_hash,
            "step_id": decision.step_id,
            "short_reason": decision.short_reason,
        },
    }


async def execute_tool(state: ProcurementCaseState) -> dict[str, Any]:
    call = state["current_tool_call"]
    decision = ProcurementNextAction(
        action="tool",
        step_id=call.get("step_id"),
        tool_name=call["tool_name"],
        arguments=call["arguments"],
        short_reason=call["short_reason"],
    )
    try:
        evidence = await state["runtime"].execute_tool(
            state,
            decision,
            call["args_hash"],
        )
        return {"current_observation": evidence.model_dump(mode="json")}
    except Exception as exc:
        return {
            "case_status": ProcurementCaseStatus.HUMAN_REQUIRED.value,
            "stop_reason": f"Read-only инструмент завершился ошибкой: {type(exc).__name__}.",
            "current_observation": None,
        }


async def save_observation(state: ProcurementCaseState) -> dict[str, Any]:
    evidence = ProcurementEvidence.model_validate(state["current_observation"])
    decision = ProcurementNextAction.model_validate(state["next_action"])
    items = [
        ProcurementEvidence.model_validate(item)
        for item in state.get("evidence") or []
    ]
    if not any(item.evidence_id == evidence.evidence_id for item in items):
        items.append(evidence)
        await state["runtime"].add_evidence(evidence, decision.step_id)
    if decision.step_id:
        await state["runtime"].planning.update_step(
            step_id=decision.step_id,
            status=(
                "completed"
                if evidence.status == "success"
                else "blocked"
            ),
            result_summary=(
                f"Получено доказательство {evidence.evidence_id}"
                if evidence.status == "success"
                else evidence.error_message
            ),
            blocking_reason=(evidence.error_message if evidence.status != "success" else None),
        )
    result: dict[str, Any] = {
        "evidence": [item.model_dump(mode="json") for item in items],
        "current_observation": evidence.model_dump(mode="json"),
    }
    if evidence.status == "capability_unavailable":
        result.update(
            case_status=ProcurementCaseStatus.HUMAN_REQUIRED.value,
            stop_reason=f"Обязательная возможность MCP недоступна: {evidence.tool_name}.",
        )
    elif evidence.status != "success":
        result.update(
            case_status=ProcurementCaseStatus.HUMAN_REQUIRED.value,
            stop_reason=f"Не удалось получить доказательство: {evidence.tool_name}.",
        )
    elif evidence.freshness_status != "fresh":
        result.update(
            case_status=ProcurementCaseStatus.HUMAN_REQUIRED.value,
            stop_reason=(
                "Доказательство не имеет подтверждённой актуальности: "
                f"{evidence.tool_name}."
            ),
        )
    await state["runtime"].save_checkpoint({**state, **result})
    return result


async def evaluate_goal(state: ProcurementCaseState) -> dict[str, Any]:
    iteration = int(state.get("iteration") or 0) + 1
    if iteration >= config.MAX_LOOP_ITERATIONS:
        return {
            "iteration": iteration,
            "case_status": ProcurementCaseStatus.BLOCKED.value,
            "stop_reason": "Достигнуто максимальное количество итераций агентного цикла.",
        }
    await state["runtime"].save_checkpoint({**state, "iteration": iteration})
    return {"iteration": iteration, "next_action": None}


async def replan(state: ProcurementCaseState) -> dict[str, Any]:
    decision = ProcurementNextAction.model_validate(state["next_action"])
    try:
        plan = await state["runtime"].replan(decision)
        await state["runtime"].save_checkpoint(state, plan=plan)
        return {"plan": plan.model_dump(mode="json"), "next_action": None}
    except (ValueError, RuntimeError) as exc:
        return {
            "case_status": ProcurementCaseStatus.HUMAN_REQUIRED.value,
            "stop_reason": f"Не удалось безопасно изменить план: {type(exc).__name__}.",
        }


async def calculate_coverage_result(state: ProcurementCaseState) -> dict[str, Any]:
    result = await state["runtime"].calculate_coverage(state)
    if result.status == "data_insufficient":
        return {
            "coverage_result": result.model_dump(mode="json"),
            "human_action": (
                result.human_action_required.model_dump(mode="json")
                if result.human_action_required
                else None
            ),
            "case_status": ProcurementCaseStatus.HUMAN_REQUIRED.value,
            "stop_reason": "Недостаточно достоверных данных для завершения КТ1.",
        }
    await state["runtime"].planning.complete_plan()
    await state["runtime"].write_event(
        "case_completed",
        {
            "coverage_status": result.status,
            "critical_positions": result.critical_positions,
            "evidence_ids": result.evidence_ids,
        },
    )
    return {
        "coverage_result": result.model_dump(mode="json"),
        "case_status": ProcurementCaseStatus.CLOSED.value,
        "recommendation": result.recommended_next_step,
    }


async def require_human(state: ProcurementCaseState) -> dict[str, Any]:
    human_action = state.get("human_action")
    if human_action is None:
        evidence_ids = [
            item.get("evidence_id")
            for item in state.get("evidence") or []
            if item.get("evidence_id")
        ]
        human_action = ProcurementHumanActionCard(
            stopped_by=state.get("stop_reason") or "Агенту требуется решение пользователя.",
            obtained_data=evidence_ids,
            requested_from_human=[state.get("stop_reason") or "Уточнить данные."],
            options=["Уточнить данные", "Восстановить возможность MCP", "Завершить кейс вручную"],
            risks=["Продолжение без подтверждения может исказить расчёт обеспеченности."],
            evidence_ids=evidence_ids,
        ).model_dump(mode="json")
    plan = state["runtime"].planning.get_active_plan()
    if plan is not None:
        await state["runtime"].planning.block_plan(state.get("stop_reason") or "human_required")
    await state["runtime"].write_event(
        "human_input_required",
        {
            "reason": state.get("stop_reason"),
            "human_action": human_action,
        },
    )
    return {
        "case_status": ProcurementCaseStatus.HUMAN_REQUIRED.value,
        "human_action": human_action,
        "requires_human_review": True,
    }


async def block_case(state: ProcurementCaseState) -> dict[str, Any]:
    plan = state["runtime"].planning.get_active_plan()
    if plan is not None:
        await state["runtime"].planning.block_plan(state.get("stop_reason") or "blocked")
    await state["runtime"].write_event(
        "case_blocked",
        {"reason": state.get("stop_reason"), "iteration": state.get("iteration", 0)},
    )
    return {"case_status": ProcurementCaseStatus.BLOCKED.value}


def finalize_result(state: ProcurementCaseState) -> dict[str, Any]:
    case_status = state.get("case_status")
    coverage = state.get("coverage_result")
    if case_status == ProcurementCaseStatus.CLOSED.value and coverage:
        summary = f"КТ1 завершён: статус обеспеченности — {coverage.get('status')}."
        status = "completed"
    elif case_status == ProcurementCaseStatus.HUMAN_REQUIRED.value:
        summary = state.get("stop_reason") or "Для продолжения требуется участие человека."
        status = "completed_with_issues"
    else:
        summary = state.get("stop_reason") or "Кейс заблокирован техническими ограничениями."
        status = "failed"
    return {
        "summary": summary,
        "status": status,
        "requires_human_review": case_status == ProcurementCaseStatus.HUMAN_REQUIRED.value,
    }


def _after_validation(state: ProcurementCaseState) -> str:
    if state.get("case_status") == ProcurementCaseStatus.HUMAN_REQUIRED.value:
        return "require_human"
    if state.get("case_status") == ProcurementCaseStatus.BLOCKED.value:
        return "block_case"
    return "check_data_quality"


def _after_data_quality(state: ProcurementCaseState) -> str:
    return (
        "require_human"
        if state.get("case_status") == ProcurementCaseStatus.HUMAN_REQUIRED.value
        else "load_case_context"
    )


def _after_plan(state: ProcurementCaseState) -> str:
    return (
        "require_human"
        if state.get("case_status") == ProcurementCaseStatus.HUMAN_REQUIRED.value
        else "select_next_action"
    )


def _after_decision(state: ProcurementCaseState) -> str:
    if state.get("case_status") == ProcurementCaseStatus.HUMAN_REQUIRED.value:
        return "require_human"
    action = (state.get("next_action") or {}).get("action")
    return {
        "tool": "policy_gate",
        "replan": "replan",
        "complete": "calculate_coverage_result",
        "human_required": "require_human",
    }.get(action, "require_human")


def _after_policy(state: ProcurementCaseState) -> str:
    return (
        "block_case"
        if state.get("case_status") == ProcurementCaseStatus.BLOCKED.value
        else (
            "require_human"
            if state.get("case_status") == ProcurementCaseStatus.HUMAN_REQUIRED.value
            else "execute_tool"
        )
    )


def _after_tool(state: ProcurementCaseState) -> str:
    return (
        "require_human"
        if state.get("case_status") == ProcurementCaseStatus.HUMAN_REQUIRED.value
        else "save_observation"
    )


def _after_observation(state: ProcurementCaseState) -> str:
    return (
        "require_human"
        if state.get("case_status") == ProcurementCaseStatus.HUMAN_REQUIRED.value
        else "evaluate_goal"
    )


def _after_goal(state: ProcurementCaseState) -> str:
    return (
        "block_case"
        if state.get("case_status") == ProcurementCaseStatus.BLOCKED.value
        else "select_next_action"
    )


def _after_replan(state: ProcurementCaseState) -> str:
    return (
        "require_human"
        if state.get("case_status") == ProcurementCaseStatus.HUMAN_REQUIRED.value
        else "select_next_action"
    )


def _after_coverage(state: ProcurementCaseState) -> str:
    return (
        "require_human"
        if state.get("case_status") == ProcurementCaseStatus.HUMAN_REQUIRED.value
        else "finalize_result"
    )


NODE_SEQUENCE = [
    ("validate_request", validate_request),
    ("check_data_quality", check_data_quality),
    ("load_case_context", load_case_context),
    ("ensure_plan", ensure_plan),
    ("select_next_action", select_next_action),
    ("policy_gate", policy_gate),
    ("execute_tool", execute_tool),
    ("save_observation", save_observation),
    ("evaluate_goal", evaluate_goal),
    ("replan", replan),
    ("calculate_coverage_result", calculate_coverage_result),
    ("require_human", require_human),
    ("block_case", block_case),
    ("finalize_result", finalize_result),
]


def build_graph():
    graph = StateGraph(ProcurementCaseState)
    for name, node in NODE_SEQUENCE:
        graph.add_node(name, node)
    graph.set_entry_point("validate_request")
    graph.add_conditional_edges("validate_request", _after_validation)
    graph.add_conditional_edges("check_data_quality", _after_data_quality)
    graph.add_edge("load_case_context", "ensure_plan")
    graph.add_conditional_edges("ensure_plan", _after_plan)
    graph.add_conditional_edges("select_next_action", _after_decision)
    graph.add_conditional_edges("policy_gate", _after_policy)
    graph.add_conditional_edges("execute_tool", _after_tool)
    graph.add_conditional_edges("save_observation", _after_observation)
    graph.add_conditional_edges("evaluate_goal", _after_goal)
    graph.add_conditional_edges("replan", _after_replan)
    graph.add_conditional_edges("calculate_coverage_result", _after_coverage)
    graph.add_edge("require_human", "finalize_result")
    graph.add_edge("block_case", "finalize_result")
    graph.add_edge("finalize_result", END)
    return graph.compile()


procurement_graph = build_graph()


__all__ = ["NODE_SEQUENCE", "build_graph", "procurement_graph"]
