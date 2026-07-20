from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.agents.procurement_agent.config import AGENT_ID
from app.agents.procurement_agent.schemas import (
    ProcurementEvidence,
    ProcurementPlan,
    ProcurementPlanStep,
)
from app.models.procurement import ProcurementCase


PlanEventWriter = Callable[[str, dict[str, Any]], Awaitable[None]]


class ProcurementPlanningAdapter:
    """Persisted in-case planning adapter; replaceable by a future Planning MCP."""

    def __init__(self, case: ProcurementCase, write_event: PlanEventWriter) -> None:
        self.case = case
        self._write_event = write_event

    def get_active_plan(self) -> ProcurementPlan | None:
        metadata = self.case.case_metadata or {}
        raw_plan = metadata.get("active_plan")
        if not raw_plan:
            return None
        plan = ProcurementPlan.model_validate(raw_plan)
        return plan if plan.status == "active" else None

    async def create_plan(
        self,
        *,
        goal: str,
        steps: list[ProcurementPlanStep],
        expected_evidence: list[str],
        dependencies: list[str] | None = None,
    ) -> ProcurementPlan:
        if self.get_active_plan() is not None:
            raise ValueError("Active procurement plan already exists")
        plan = ProcurementPlan(
            plan_id=str(uuid.uuid4()),
            case_id=str(self.case.id),
            agent_id=AGENT_ID,
            goal=goal,
            steps=steps,
            dependencies=dependencies or [],
            expected_evidence=expected_evidence,
        )
        self._store(plan)
        await self._write_event("plan_created", _plan_event_payload(plan))
        return plan

    async def update_step(
        self,
        *,
        step_id: str,
        status: str,
        result_summary: str | None = None,
        blocking_reason: str | None = None,
    ) -> ProcurementPlan:
        plan = self._require_active()
        step = next((item for item in plan.steps if item.step_id == step_id), None)
        if step is None:
            raise KeyError(f"Unknown plan step: {step_id}")
        previous_status = step.status
        step.status = status  # type: ignore[assignment]
        step.result_summary = result_summary
        step.blocking_reason = blocking_reason
        self._store(plan)
        event_type = "plan_step_started" if status == "running" else "plan_step_updated"
        await self._write_event(
            event_type,
            {
                "plan_id": plan.plan_id,
                "version": plan.version,
                "step_id": step_id,
                "previous_status": previous_status,
                "status": status,
                "result_summary": result_summary,
                "blocking_reason": blocking_reason,
            },
        )
        return plan

    async def add_evidence(self, evidence: ProcurementEvidence, step_id: str | None = None) -> None:
        metadata = dict(self.case.case_metadata or {})
        evidence_items = list(metadata.get("evidence") or [])
        if not any(item.get("evidence_id") == evidence.evidence_id for item in evidence_items):
            evidence_items.append(evidence.model_dump(mode="json"))
            metadata["evidence"] = evidence_items
            self.case.case_metadata = metadata
            await self._write_event(
                "evidence_added",
                {
                    "evidence_id": evidence.evidence_id,
                    "step_id": step_id,
                    "tool_name": evidence.tool_name,
                    "object_type": evidence.object_type,
                    "object_id": evidence.object_id,
                    "freshness_status": evidence.freshness_status,
                    "status": evidence.status,
                    "content_hash": evidence.content_hash,
                },
            )

    async def replan(
        self,
        *,
        reason: str,
        steps: list[ProcurementPlanStep],
        expected_evidence: list[str],
    ) -> ProcurementPlan:
        plan = self._require_active()
        previous_version = plan.version
        plan.version += 1
        plan.steps = steps
        plan.expected_evidence = expected_evidence
        plan.replan_reason = reason
        self._store(plan)
        await self._write_event(
            "plan_replanned",
            {
                "plan_id": plan.plan_id,
                "previous_version": previous_version,
                "version": plan.version,
                "reason": reason,
                "step_ids": [step.step_id for step in steps],
                "expected_evidence": expected_evidence,
            },
        )
        return plan

    async def complete_plan(self) -> ProcurementPlan:
        plan = self._require_active()
        plan.status = "completed"
        plan.completed_at = datetime.now(UTC)
        self._store(plan)
        await self._write_event(
            "plan_completed",
            {
                "plan_id": plan.plan_id,
                "version": plan.version,
                "completed_at": plan.completed_at.isoformat(),
            },
        )
        return plan

    async def block_plan(self, reason: str) -> ProcurementPlan:
        plan = self._require_active()
        plan.status = "blocked"
        plan.replan_reason = reason
        self._store(plan)
        await self._write_event(
            "plan_blocked",
            {"plan_id": plan.plan_id, "version": plan.version, "reason": reason},
        )
        return plan

    def _require_active(self) -> ProcurementPlan:
        plan = self.get_active_plan()
        if plan is None:
            raise RuntimeError("No active procurement plan")
        return plan

    def _store(self, plan: ProcurementPlan) -> None:
        metadata = dict(self.case.case_metadata or {})
        metadata["active_plan"] = plan.model_dump(mode="json")
        self.case.case_metadata = metadata


def _plan_event_payload(plan: ProcurementPlan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "version": plan.version,
        "goal": plan.goal,
        "status": plan.status,
        "steps": [
            {
                "step_id": step.step_id,
                "objective": step.objective,
                "status": step.status,
                "allowed_tool_categories": step.allowed_tool_categories,
                "required_evidence": step.required_evidence,
                "dependencies": step.dependencies,
            }
            for step in plan.steps
        ],
        "expected_evidence": plan.expected_evidence,
    }


__all__ = ["ProcurementPlanningAdapter"]
