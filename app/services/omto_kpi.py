"""Вычисление KPI ролевых агентов ОМТО из реальной истории запусков.

Значения KPI считаются из таблицы ``agent_kpi_runs`` (фактические запуски агента).
Дескрипторы KPI (цель, способ получения) заданы в
:mod:`app.agents.omto_role_agents.catalog`.

Пути получения значения:

* ``metric`` — агрегат по истории запусков (реальные данные платформы);
* ``provider="onec"`` — требует сверки с 1С/ОПЭ. Значение поставляет подключаемый
  :class:`OneCKpiProvider`. По умолчанию (:class:`NullOneCKpiProvider`) значение —
  ``None`` со статусом ``pending_integration``: честный «ожидает интеграции 1С»
  вместо выдуманного числа. Подключение реального источника 1С позже НЕ требует
  изменений в дашборде — достаточно передать другой провайдер.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.omto_role_agents.catalog import KpiDescriptor, OmtoAgentSpec, get_spec
from app.models.agent_kpi import AgentKpiRun

MAX_RUNS_WINDOW = 1000


class RunsAggregate:
    """Агрегаты по истории запусков одного агента."""

    def __init__(self, runs: list[AgentKpiRun]) -> None:
        self.total = len(runs)
        self.completed = sum(1 for r in runs if r.status == "completed")
        self.with_issues = sum(1 for r in runs if r.status == "completed_with_issues")
        self.needs_input = sum(1 for r in runs if r.status == "needs_input")
        self.failed = sum(1 for r in runs if r.status == "failed")
        self.waiting_human = sum(1 for r in runs if r.role_status == "waiting_human")
        self.hitl_required = sum(1 for r in runs if r.requires_human_review)
        self.total_findings = sum(r.total_findings or 0 for r in runs)
        self.critical_findings = sum(r.critical_findings or 0 for r in runs)
        self.findings_with_source = sum(r.findings_with_source or 0 for r in runs)
        self._coverages = [
            float(r.coverage_percent)
            for r in runs
            if r.coverage_percent is not None
        ]
        self.verdicts_below_coverage = sum(
            1
            for r in runs
            if r.verdict_emitted
            and r.coverage_percent is not None
            and float(r.coverage_percent) < 100
        )
        latencies = [r.latency_ms or 0 for r in runs]
        self.avg_latency_ms = round(sum(latencies) / len(latencies)) if latencies else 0
        self.last_run_at = max((r.created_at for r in runs), default=None)

    def metric(self, key: str) -> float | None:
        if key == "source_reference_rate":
            if self.total_findings <= 0:
                return None
            return round(100 * self.findings_with_source / self.total_findings, 1)
        if key == "avg_coverage":
            if not self._coverages:
                return None
            return round(sum(self._coverages) / len(self._coverages), 1)
        if key == "verdicts_below_full_coverage":
            if not self._coverages:
                return None
            return float(self.verdicts_below_coverage)
        return None


class OneCKpiProvider(Protocol):
    """Источник значений KPI, требующих сверки с 1С/ОПЭ (подключается позже)."""

    async def value(
        self, slug: str, kpi: KpiDescriptor, agg: RunsAggregate
    ) -> float | None: ...


class NullOneCKpiProvider:
    """Заглушка: интеграция 1С не подключена — значение отсутствует (не фейк)."""

    async def value(
        self, slug: str, kpi: KpiDescriptor, agg: RunsAggregate
    ) -> float | None:
        return None


def _evaluate_status(value: float | None, kpi: KpiDescriptor) -> tuple[str, bool | None]:
    """Статус KPI относительно цели: achieved | warn | below | no_data."""
    if value is None:
        return "no_data", None
    goal = kpi.goal
    if goal.op == ">=":
        if value >= goal.value:
            return "achieved", True
        if value >= goal.value * 0.95:
            return "warn", False
        return "below", False
    if goal.op == "<=":
        if value <= goal.value:
            return "achieved", True
        if value <= max(goal.value * 1.5, goal.value + 2):
            return "warn", False
        return "below", False
    # op == "=="  (обычно цель 0 для blocking-KPI)
    if value == goal.value:
        return "achieved", True
    return "below", False


async def _load_runs(
    db: AsyncSession, slug: str, tenant_id: str | None
) -> list[AgentKpiRun]:
    stmt = select(AgentKpiRun).where(AgentKpiRun.agent_slug == slug)
    if tenant_id:
        stmt = stmt.where(AgentKpiRun.tenant_id == tenant_id)
    stmt = stmt.order_by(AgentKpiRun.created_at.desc()).limit(MAX_RUNS_WINDOW)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def compute_dashboard(
    db: AsyncSession,
    slug: str,
    *,
    tenant_id: str | None = None,
    onec_provider: OneCKpiProvider | None = None,
) -> dict:
    spec = get_spec(slug)
    if spec is None:
        raise ValueError(f"Неизвестный ролевой агент ОМТО: {slug!r}")
    provider: OneCKpiProvider = onec_provider or NullOneCKpiProvider()

    runs = await _load_runs(db, slug, tenant_id)
    agg = RunsAggregate(runs)

    kpi_rows: list[dict] = []
    achieved = warn = below = pending = 0
    for kpi in spec.kpi:
        if kpi.provider == "onec":
            value = await provider.value(slug, kpi, agg)
            data_source = "onec"
        else:
            value = agg.metric(kpi.metric) if kpi.metric else None
            data_source = "runs"

        status, is_achieved = _evaluate_status(value, kpi)
        if value is None:
            status = "pending_integration" if data_source == "onec" else "no_data"
            pending += 1
        elif status == "achieved":
            achieved += 1
        elif status == "warn":
            warn += 1
        else:
            below += 1

        kpi_rows.append(
            {
                "id": kpi.id,
                "name": kpi.name,
                "target": kpi.target,
                "unit": kpi.goal.unit,
                "blocking": kpi.blocking,
                "guardrail": kpi.guardrail,
                "source": kpi.source,
                "data_source": data_source,
                "value": value,
                "status": status,
                "achieved": is_achieved,
            }
        )

    known = achieved + warn + below
    return {
        "agent": _agent_passport(spec),
        "runtime": {
            "total_runs": agg.total,
            "completed": agg.completed,
            "with_issues": agg.with_issues,
            "needs_input": agg.needs_input,
            "failed": agg.failed,
            "waiting_human": agg.waiting_human,
            "hitl_required": agg.hitl_required,
            "avg_latency_ms": agg.avg_latency_ms,
            "last_run_at": agg.last_run_at.isoformat() if agg.last_run_at else None,
        },
        "kpi": kpi_rows,
        "summary": {
            "total": len(spec.kpi),
            "achieved": achieved,
            "warn": warn,
            "below": below,
            "pending": pending,
            "blocking": sum(1 for k in spec.kpi if k.blocking),
            "guardrail": sum(1 for k in spec.kpi if k.guardrail),
            "achievement_rate": round(100 * achieved / known, 1) if known else None,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _agent_passport(spec: OmtoAgentSpec) -> dict:
    return {
        "slug": spec.slug,
        "name": spec.name,
        "name_full": spec.name_full,
        "doc_ref": spec.doc_ref,
        "registry_no": spec.registry_no,
        "position_role": spec.position_role,
        "purpose": spec.purpose,
        "contour": spec.contour,
        "autonomy": spec.autonomy_default,
    }


__all__ = [
    "NullOneCKpiProvider",
    "OneCKpiProvider",
    "RunsAggregate",
    "compute_dashboard",
]
