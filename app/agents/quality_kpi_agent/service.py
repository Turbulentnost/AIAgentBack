"""Quality KPI meta-agent — evaluates other agents per §12 (parallel per agent)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.agents.common.base import BaseAgent
from app.agents.procurement_role_agents import config
from app.agents.procurement_role_agents.schemas import (
    ProcurementRoleAgentRequest,
    ProcurementRoleAgentResult,
)
from app.agents.quality_kpi_agent.formulas import (
    compute_common_kpis,
    compute_special_kpis,
    compute_system_quality_kpis,
)
from app.agents.quality_kpi_agent.schemas import AgentKpiBlock, QualityKpiReport
from app.models.enums import ConfidenceLevel

DEFAULT_AGENT_IDS = (
    config.OTK_HEAD_AGENT_ID,
    config.QUALITY_ENGINEER_AGENT_ID,
    config.QUALITY_DEPUTY_DIRECTOR_AGENT_ID,
    config.OMTO_SUPPORT_MANAGER_AGENT_ID,
    config.PRODUCTION_PREPARATION_ENGINEER_AGENT_ID,
)


def _task_from_event(event: dict[str, Any], agent_id: str) -> dict[str, Any] | None:
    if event.get("agent_id") and event.get("agent_id") != agent_id:
        return None
    output = event.get("output_data") or {}
    if not isinstance(output, dict):
        output = {}
    role_status = event.get("role_status") or event.get("status")
    checked = role_status in {"completed", "waiting_human"} or bool(event.get("checked"))
    findings = output.get("findings") or []
    has_critical = any(
        isinstance(f, dict) and f.get("severity") == "critical" for f in findings
    )
    return {
        "checked": checked or bool(event.get("checked", True)),
        "confirmed_without_material_error": event.get(
            "confirmed_without_material_error",
            not has_critical and role_status != "failed",
        ),
        "completeness_ok": event.get(
            "completeness_ok",
            not any(
                isinstance(f, dict) and "не заполнен" in str(f.get("message", "")).lower()
                for f in findings
            ),
        ),
        "sla_met": event.get("sla_met", True),
        "substantially_reworked": event.get("substantially_reworked", False),
        "traceability_ok": event.get(
            "traceability_ok",
            bool(output.get("quality_control") or output.get("actions") or event.get("rule_refs")),
        ),
        "critical_unauthorized": event.get("critical_unauthorized", False),
        # Special flags — default optimistic for MVP when absent.
        "assigned_within_2wh": event.get("assigned_within_2wh", True),
        "act_confirmed_within_1wh": event.get("act_confirmed_within_1wh", True),
        "handed_to_zdk_by_1600": event.get("handed_to_zdk_by_1600", True),
        "missed_critical": event.get("missed_critical", False),
        "docs_complete": event.get("docs_complete", not has_critical),
        "program_ok": event.get("program_ok", True),
        "results_complete": event.get("results_complete", True),
        "false_releasing_status": event.get("false_releasing_status", False),
        "act_label_timely": event.get("act_label_timely", True),
        "resolution_within_8wh": event.get("resolution_within_8wh", True),
        "disposition_allowed": event.get(
            "disposition_allowed",
            output.get("within_allowed_list", True),
        ),
        "conditions_complete": event.get(
            "conditions_complete",
            bool(output.get("execution_conditions")),
        ),
        "contradictory_resolution": event.get("contradictory_resolution", False),
    }


async def _compute_agent_block(
    agent_id: str,
    events: list[dict[str, Any]],
) -> AgentKpiBlock:
    tasks = []
    for event in events:
        item = _task_from_event(event, agent_id)
        if item is not None:
            tasks.append(item)
    # If no events for agent, still emit empty metrics block.
    common = compute_common_kpis(tasks)
    special = compute_special_kpis(agent_id, tasks)
    below = [
        m.id
        for m in [*common, *special]
        if m.tone in {"warn", "bad"}
    ]
    return AgentKpiBlock(
        agent_id=agent_id,
        agent_label=config.agent_label(agent_id) or agent_id,
        common=common,
        special=special,
        below_target=below,
    )


class QualityKpiService:
    async def run(self, payload: dict[str, Any], *, agent_id: str) -> ProcurementRoleAgentResult:
        # KPI agent accepts either role-agent request or loose dashboard payload.
        case_id = str(payload.get("case_id") or "kpi-dashboard")
        correlation_id = str(payload.get("correlation_id") or case_id)
        try:
            if "source_type" in payload and "idempotency_key" in payload:
                request = ProcurementRoleAgentRequest.model_validate(payload)
                case_id = request.case_id
                correlation_id = request.correlation_id
                source_data = request.source_data
                role_context = request.role_context
            else:
                source_data = dict(payload.get("source_data") or payload)
                role_context = dict(payload.get("role_context") or {})
        except ValidationError as exc:
            return ProcurementRoleAgentResult(
                agent_id=agent_id,
                status="failed",
                summary="Входные данные KPI-агента не прошли проверку.",
                data_confidence=ConfidenceLevel.LOW,
                requires_human_review=False,
                case_id=case_id,
                correlation_id=correlation_id,
                role_status="failed",
                output_data={"validation_errors": exc.errors()},
            )

        agent_ids = list(
            role_context.get("agent_ids")
            or source_data.get("agent_ids")
            or DEFAULT_AGENT_IDS
        )
        events = list(source_data.get("agent_events") or role_context.get("agent_events") or [])
        cases = list(source_data.get("quality_cases") or role_context.get("quality_cases") or [])
        period_from = source_data.get("period_from") or role_context.get("period_from")
        period_to = source_data.get("period_to") or role_context.get("period_to")

        blocks = await asyncio.gather(
            *[_compute_agent_block(aid, events) for aid in agent_ids]
        )
        system = compute_system_quality_kpis(cases)
        below_agents = [b.agent_id for b in blocks if b.below_target]
        now = datetime.now(timezone.utc)
        report = QualityKpiReport(
            period_from=str(period_from) if period_from else None,
            period_to=str(period_to) if period_to else None,
            agents=list(blocks),
            system=system,
            summary=(
                f"KPI по {len(blocks)} агентам. "
                f"Ниже цели: {len(below_agents)}."
                if below_agents
                else f"KPI по {len(blocks)} агентам — отклонения не выявлены."
            ),
            calculated_at=now,
            actions=["KPI_REPORT"],
        )
        return ProcurementRoleAgentResult(
            agent_id=agent_id,
            status="completed",
            summary=report.summary,
            data_confidence=ConfidenceLevel.HIGH,
            requires_human_review=False,
            case_id=case_id,
            correlation_id=correlation_id,
            role_status="completed",
            output_data=report.model_dump(mode="json"),
        )


# Thin BaseAgent for non-procurement dashboard invocation.
class QualityKpiStandaloneAgent(BaseAgent):
    agent_id = config.QUALITY_KPI_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = "Оценка работы ИИ-агентов и расчёт KPI по §12 ТЗ."
    version = config.AGENT_VERSION

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        return await QualityKpiService().run(payload, agent_id=self.agent_id)


__all__ = ["QualityKpiService", "QualityKpiStandaloneAgent"]
