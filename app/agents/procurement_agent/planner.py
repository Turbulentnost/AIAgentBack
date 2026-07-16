from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

import app.tools  # noqa: F401
from app.agents.procurement_agent.config import READ_ONLY_TOOL_NAMES
from app.agents.procurement_agent.llm_client import ProcurementLLMError, procurement_llm_client
from app.agents.procurement_agent.schemas import (
    ProcurementEvidence,
    ProcurementPlan,
    ProcurementPlanStep,
)
from app.tools.registry import tool_registry


class PlannerUnavailableError(RuntimeError):
    pass


class ProcurementNextAction(BaseModel):
    action: Literal["tool", "complete", "replan", "human_required"]
    step_id: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    short_reason: str = Field(..., min_length=1, max_length=1000)
    replan_steps: list[ProcurementPlanStep] = Field(default_factory=list)
    expected_evidence: list[str] = Field(default_factory=list)
    human_request: list[str] = Field(default_factory=list)


class ProcurementPlannerProtocol(Protocol):
    async def create_plan(
        self,
        *,
        case_id: str,
        correlation_id: str,
        source_type: str,
        source_1c_ref: str,
        source_data: dict[str, Any],
    ) -> tuple[str, list[ProcurementPlanStep], list[str]]:
        ...

    async def decide_next(
        self,
        *,
        plan: ProcurementPlan,
        source_data: dict[str, Any],
        evidence: list[ProcurementEvidence],
        iteration: int,
    ) -> ProcurementNextAction:
        ...


class LLMProcurementPlanner:
    async def create_plan(
        self,
        *,
        case_id: str,
        correlation_id: str,
        source_type: str,
        source_1c_ref: str,
        source_data: dict[str, Any],
    ) -> tuple[str, list[ProcurementPlanStep], list[str]]:
        response = await _chat_json(
            [
                {"role": "system", "content": _planning_system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "create_plan",
                            "case_id": case_id,
                            "correlation_id": correlation_id,
                            "source_type": source_type,
                            "source_1c_ref": source_1c_ref,
                            "available_source_fields": sorted(source_data.keys()),
                            "source_summary": _safe_source_summary(source_data),
                            "allowed_tools": _tool_catalog(),
                            "output_contract": {
                                "goal": "string",
                                "steps": [
                                    {
                                        "step_id": "string",
                                        "objective": "string",
                                        "status": "pending",
                                        "allowed_tool_categories": ["onec_read"],
                                        "required_evidence": ["string"],
                                        "dependencies": ["step_id"],
                                        "result_summary": None,
                                        "blocking_reason": None,
                                    }
                                ],
                                "expected_evidence": ["string"],
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        payload = _response_json(response)
        try:
            goal = str(payload["goal"])
            steps = [ProcurementPlanStep.model_validate(item) for item in payload["steps"]]
            expected = [str(item) for item in payload.get("expected_evidence") or []]
        except (KeyError, TypeError, ValueError) as exc:
            raise PlannerUnavailableError("LLM returned an invalid procurement plan") from exc
        if not steps:
            raise PlannerUnavailableError("LLM returned an empty procurement plan")
        return goal, steps, expected

    async def decide_next(
        self,
        *,
        plan: ProcurementPlan,
        source_data: dict[str, Any],
        evidence: list[ProcurementEvidence],
        iteration: int,
    ) -> ProcurementNextAction:
        response = await _chat_json(
            [
                {"role": "system", "content": _planning_system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "next_action",
                            "iteration": iteration,
                            "plan": plan.model_dump(mode="json"),
                            "available_source_fields": sorted(source_data.keys()),
                            "source_summary": _safe_source_summary(source_data),
                            "evidence": [_compact_evidence(item) for item in evidence[-20:]],
                            "allowed_tools": _tool_catalog(),
                            "output_contract": {
                                "action": "tool|complete|replan|human_required",
                                "step_id": "string|null",
                                "tool_name": "allowed tool name|null",
                                "arguments": {},
                                "short_reason": "short string",
                                "replan_steps": [],
                                "expected_evidence": [],
                                "human_request": [],
                            },
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
        )
        try:
            decision = ProcurementNextAction.model_validate(_response_json(response))
        except ValueError as exc:
            raise PlannerUnavailableError("LLM returned an invalid next action") from exc
        if decision.action == "tool" and decision.tool_name not in READ_ONLY_TOOL_NAMES:
            raise PlannerUnavailableError("LLM selected a tool outside the read-only allowlist")
        return decision


def _planning_system_prompt() -> str:
    return (
        "Ты планировщик Level 0 агента закупок. Составляй и корректируй план "
        "проверки обеспеченности КТ1. Разрешены только перечисленные read-only "
        "инструменты 1С. Нельзя создавать/изменять/проводить документы или "
        "отправлять сообщения. Не раскрывай цепочку рассуждений: возвращай только "
        "JSON с планом либо выбранным действием и кратким основанием short_reason. "
        "Для tool action используй строго входную схему инструмента. Если "
        "обязательная возможность недоступна, выбери human_required. Если во входе "
        "уже есть positions, не запрашивай строки потребности повторно. Для проверки "
        "остатка сначала используй onec_get_free_stock."
    )


def _tool_catalog() -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for name in READ_ONLY_TOOL_NAMES:
        tool = tool_registry.get(name)
        if tool is None:
            continue
        catalog.append(
            {
                "name": name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "category": "onec_read",
                "action_class": "R",
            }
        )
    return catalog


def _compact_evidence(evidence: ProcurementEvidence) -> dict[str, Any]:
    return {
        "evidence_id": evidence.evidence_id,
        "tool_name": evidence.tool_name,
        "object_type": evidence.object_type,
        "object_id": evidence.object_id,
        "retrieved_at": evidence.retrieved_at.isoformat(),
        "business_effective_at": (
            evidence.business_effective_at.isoformat() if evidence.business_effective_at else None
        ),
        "freshness_status": evidence.freshness_status,
        "status": evidence.status,
        "error_code": evidence.error_code,
        "data": evidence.data,
    }


def _safe_source_summary(source_data: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "positions",
        "warehouse_ids",
        "organization_id",
        "requested_date",
        "nomenclature_ref",
        "quantity",
        "unit",
    }
    return {
        key: value
        for key, value in source_data.items()
        if key in allowed_keys
    }


def _response_json(response: dict[str, Any]) -> dict[str, Any]:
    message = (response.get("choices") or [{}])[0].get("message") or {}
    content = (message.get("content") or "").strip()
    if "</think>" in content:
        content = content.rsplit("</think>", 1)[-1].strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        start = content.find("{")
        if start < 0:
            raise PlannerUnavailableError("LLM response is not valid JSON") from exc
        try:
            payload, _ = json.JSONDecoder().raw_decode(content[start:])
        except json.JSONDecodeError as nested_exc:
            raise PlannerUnavailableError("LLM response is not valid JSON") from nested_exc
    if not isinstance(payload, dict):
        raise PlannerUnavailableError("LLM response must be a JSON object")
    return payload


async def _chat_json(messages: list[dict[str, str]]) -> dict[str, Any]:
    try:
        return await procurement_llm_client.chat(messages)
    except ProcurementLLMError as exc:
        raise PlannerUnavailableError("Procurement LLM is unavailable") from exc


__all__ = [
    "LLMProcurementPlanner",
    "PlannerUnavailableError",
    "ProcurementNextAction",
    "ProcurementPlannerProtocol",
]
