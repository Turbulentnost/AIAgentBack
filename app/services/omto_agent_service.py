"""Запуск и возобновление ролевого агента ОМТО с фиксацией запуска для KPI.

Это точка «вызова через оркестратор»: агент исполняется на боевом LangGraph, а
измеримые сигналы запуска сохраняются в ``agent_kpi_runs`` (из них дашборд считает
реальные KPI). Точки HITL реальны: при ``interrupt`` запуск получает статус
``waiting_human`` и данные точки; после решения человека граф возобновляется, и та
же запись обновляется итогом.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.omto_role_agents.catalog import get_spec
from app.agents.omto_role_agents.runner import (
    RunSignals,
    aresume_omto_agent,
    arun_omto_agent,
)
from app.agents.omto_role_agents.schemas import OmtoAgentRequest, OmtoAgentResult
from app.models.agent_kpi import AgentKpiRun


def _apply_signals(
    run: AgentKpiRun,
    result: OmtoAgentResult,
    signals: RunSignals,
) -> AgentKpiRun:
    run.status = signals.status
    run.role_status = signals.role_status
    run.data_confidence = signals.data_confidence
    run.requires_human_review = signals.requires_human_review
    run.total_findings = signals.total_findings
    run.critical_findings = signals.critical_findings
    run.findings_with_source = signals.findings_with_source
    run.source_references = signals.source_references
    run.coverage_percent = signals.coverage_percent
    run.verdict_emitted = signals.verdict_emitted
    run.latency_ms = signals.latency_ms
    run.summary = result.summary
    run.output_data = result.output_data
    run.hitl_pending = result.output_data.get("hitl_pending")
    return run


async def run_and_record(
    db: AsyncSession,
    slug: str,
    request: OmtoAgentRequest,
    *,
    triggered_by_id: uuid.UUID | None = None,
) -> OmtoAgentResult:
    """Исполняет агента и сохраняет запуск. Коммит выполняет вызывающий эндпоинт."""
    if get_spec(slug) is None:
        raise ValueError(f"Неизвестный ролевой агент ОМТО: {slug!r}")
    result, signals = await arun_omto_agent(slug, request, agent_id=slug)
    run = AgentKpiRun(
        agent_slug=slug,
        correlation_id=request.correlation_id,
        tenant_id=request.tenant_id,
        task_type=request.task_type,
        thread_id=request.correlation_id,
        triggered_by_id=triggered_by_id,
    )
    _apply_signals(run, result, signals)
    db.add(run)
    await db.flush()
    return result


async def resume_and_record(
    db: AsyncSession,
    slug: str,
    *,
    thread_id: str,
    decision: dict,
    triggered_by_id: uuid.UUID | None = None,
) -> OmtoAgentResult | None:
    """Возобновляет приостановленный на HITL запуск и обновляет его запись.

    Возвращает None, если по ``thread_id`` нет приостановленного запуска.
    """
    if get_spec(slug) is None:
        raise ValueError(f"Неизвестный ролевой агент ОМТО: {slug!r}")

    run = await db.scalar(
        select(AgentKpiRun)
        .where(
            AgentKpiRun.agent_slug == slug,
            AgentKpiRun.thread_id == thread_id,
            AgentKpiRun.role_status == "waiting_human",
        )
        .order_by(AgentKpiRun.created_at.desc())
    )
    if run is None:
        return None

    request = OmtoAgentRequest(
        correlation_id=thread_id,
        tenant_id=run.tenant_id,
        task_type=run.task_type or "resume",
        requested_by=str(triggered_by_id or "human"),
    )
    resumed = await aresume_omto_agent(
        slug,
        thread_id=thread_id,
        decision=decision,
        agent_id=slug,
        request=request,
    )
    if resumed is None:
        return None
    result, signals = resumed
    _apply_signals(run, result, signals)
    await db.flush()
    return result


__all__ = ["resume_and_record", "run_and_record"]
