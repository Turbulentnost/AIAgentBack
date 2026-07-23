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

DEFAULT_AGENT_IDS = config.KPI_EVALUATED_AGENT_IDS

_EXPLICIT_TASK_FLAGS = (
    "confirmed_without_material_error",
    "completeness_ok",
    "sla_met",
    "substantially_reworked",
    "traceability_ok",
    "critical_unauthorized",
    "assigned_within_2wh",
    "act_confirmed_within_1wh",
    "handed_to_zdk_by_1600",
    "missed_critical",
    "quarterly_report_complete",
    "docs_complete",
    "program_ok",
    "results_complete",
    "false_releasing_status",
    "act_label_timely",
    "recontrol_linked",
    "resolution_within_8wh",
    "disposition_allowed",
    "conditions_complete",
    "contradictory_resolution",
    "status_accuracy_ok",
    "missed_control_date",
    "docs_pack_complete",
    "delivery_forecast_ok",
    "bom_coverage_ok",
    "need_calc_ok",
    "material_order_complete",
    "missed_critical_position",
    "minmax_ok",
    "replenish_signal_timely",
    "duplicate_replenish_signal",
    "missed_rop_deficit",
    "requisites_complete",
    "route_classified_ok",
    "clarify_cycles_ok",
    "request_without_basis",
    "warehouse_task_ok",
    "defect_zone_ok",
    "receipt_without_docs",
    "warehouse_task_overdue",
    "comparable_quotes_ok",
    "comparison_complete",
    "supplier_confirmed",
    "critical_order_error",
    "budget_check_ok",
    "exception_justified",
    "finance_decision_timely",
    "over_limit_approval",
    "payment_basis_ok",
    "resolution_timely",
    "resolution_returned",
    "illegal_exception",
    "missed_accounting_error",
    "accounting_docs_ok",
    "accounting_opinion_timely",
    "accounting_corrected",
    "payment_status_ok",
    "payment_without_approval",
    "primary_docs_ok",
    "discrepancy_handled_timely",
    "contract_terms_ok",
    "missed_legal_risk",
    "legal_edit_accepted",
    "legal_opinion_timely",
    "cfo_article_ok",
    "priority_justified",
    "approval_timely",
    "budget_conflict",
)


def _task_from_event(event: dict[str, Any], agent_id: str) -> dict[str, Any] | None:
    """Build KPI task row only for matching agent; no optimistic defaults."""
    event_agent = event.get("agent_id")
    if not event_agent or event_agent != agent_id:
        return None

    output = event.get("output_data") or {}
    if not isinstance(output, dict):
        output = {}
    role_status = event.get("role_status") or event.get("status")
    checked = bool(event.get("checked")) or role_status in {
        "completed",
        "waiting_human",
        "completed_with_issues",
    }
    findings = output.get("findings") or []
    has_critical = any(
        isinstance(f, dict) and f.get("severity") == "critical" for f in findings
    )

    task: dict[str, Any] = {"checked": checked}
    for flag in _EXPLICIT_TASK_FLAGS:
        if flag in event:
            task[flag] = event[flag]

    # Derive only when explicit KPI flags are absent — never invent success.
    if "confirmed_without_material_error" not in task and checked:
        if role_status == "failed" or has_critical:
            task["confirmed_without_material_error"] = False
    if "completeness_ok" not in task and checked:
        missing = any(
            isinstance(f, dict) and "не заполнен" in str(f.get("message", "")).lower()
            for f in findings
        )
        if missing:
            task["completeness_ok"] = False
    if "traceability_ok" not in task and checked:
        has_refs = bool(
            output.get("quality_control") or output.get("actions") or event.get("rule_refs")
        )
        if has_refs:
            task["traceability_ok"] = True
    if "critical_unauthorized" not in task and has_critical:
        task["critical_unauthorized"] = True
    if "disposition_allowed" not in task and "within_allowed_list" in output:
        task["disposition_allowed"] = bool(output.get("within_allowed_list"))
    if "conditions_complete" not in task and "execution_conditions" in output:
        task["conditions_complete"] = bool(output.get("execution_conditions"))

    return task


def _worst_tone(tones: list[str]) -> str:
    if "bad" in tones:
        return "bad"
    if "warn" in tones or "unknown" in tones:
        return "warn"
    return "ok"


async def _compute_agent_block(
    agent_id: str,
    events: list[dict[str, Any]],
) -> AgentKpiBlock:
    tasks = []
    for event in events:
        item = _task_from_event(event, agent_id)
        if item is not None:
            tasks.append(item)
    common = compute_common_kpis(tasks)
    special = compute_special_kpis(agent_id, tasks)
    below = [
        m.id
        for m in [*common, *special]
        if m.tone in {"warn", "bad", "unknown"}
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
        all_tones = [m.tone for b in blocks for m in [*b.common, *b.special]] + [
            m.tone for m in system
        ]
        below_agents = [b.agent_id for b in blocks if b.below_target]
        no_data_agents = [
            b.agent_id
            for b in blocks
            if all(m.tone == "unknown" for m in [*b.common, *b.special])
        ]
        bad_count = sum(1 for tone in all_tones if tone == "bad")
        warn_count = sum(1 for tone in all_tones if tone in {"warn", "unknown"})
        worst = _worst_tone(all_tones)
        if worst == "ok":
            summary = f"KPI по {len(blocks)} агентам — отклонения не выявлены."
        elif bad_count:
            summary = (
                f"KPI по {len(blocks)} агентам: критических отклонений {bad_count}, "
                f"предупреждений {warn_count} "
                f"(агентов ниже цели / без данных: {len(below_agents)})."
            )
        else:
            summary = (
                f"KPI по {len(blocks)} агентам: предупреждений {warn_count} "
                f"(без выполненных работ: {len(no_data_agents)})."
            )

        now = datetime.now(timezone.utc)
        report = QualityKpiReport(
            period_from=str(period_from) if period_from else None,
            period_to=str(period_to) if period_to else None,
            agents=list(blocks),
            system=system,
            summary=summary,
            calculated_at=now,
            actions=["KPI_REPORT"],
        )
        return ProcurementRoleAgentResult(
            agent_id=agent_id,
            status="completed",
            summary=report.summary,
            data_confidence=ConfidenceLevel.MEDIUM if worst != "ok" else ConfidenceLevel.HIGH,
            requires_human_review=worst in {"warn", "bad"},
            case_id=case_id,
            correlation_id=correlation_id,
            role_status="completed",
            output_data=report.model_dump(mode="json"),
        )


class QualityKpiStandaloneAgent(BaseAgent):
    agent_id = config.QUALITY_KPI_AGENT_ID
    name = config.AGENT_LABELS[agent_id]
    purpose = "Оценка работы ИИ-агентов и расчёт KPI по §12 ТЗ."
    version = config.AGENT_VERSION

    async def run(self, payload: dict) -> ProcurementRoleAgentResult:
        return await QualityKpiService().run(payload, agent_id=self.agent_id)


__all__ = ["QualityKpiService", "QualityKpiStandaloneAgent"]
