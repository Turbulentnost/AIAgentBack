"""§12 KPI formulas — common, role-special, system quality metrics."""

from __future__ import annotations

from typing import Any

from app.agents.quality_kpi_agent.schemas import KpiMetric

# Targets from ТЗ §12.1 / §12.2 / §12.3
TARGET_ACCURACY = 95.0
TARGET_COMPLETENESS = 98.0
TARGET_TIMELINESS = 95.0
TARGET_REWORK_PILOT = 10.0
TARGET_TRACEABILITY = 100.0
TARGET_CRITICAL = 0.0
TARGET_INCOMING_SLA = 95.0
TARGET_LOTS_WITHOUT_STATUS = 0.0
TARGET_CONTROL_TRACEABILITY = 100.0


def _tone(value: float | None, target: float | None, *, higher_is_better: bool = True) -> str:
    if value is None or target is None:
        return "unknown"
    if higher_is_better:
        if value >= target:
            return "ok"
        if value >= target - 5:
            return "warn"
        return "bad"
    if value <= target:
        return "ok"
    if value <= target + 5:
        return "warn"
    return "bad"


def _pct(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 2)


def compute_common_kpis(tasks: list[dict[str, Any]]) -> list[KpiMetric]:
    """§12.1 common KPIs for one agent from completed/checked tasks."""
    checked = [t for t in tasks if t.get("checked")]
    n = len(checked) or len(tasks)
    sample = n
    confirmed = sum(1 for t in checked if t.get("confirmed_without_material_error"))
    complete = sum(1 for t in checked if t.get("completeness_ok"))
    timely = sum(1 for t in checked if t.get("sla_met"))
    reworked = sum(1 for t in checked if t.get("substantially_reworked"))
    traced = sum(1 for t in checked if t.get("traceability_ok"))
    critical = sum(1 for t in checked if t.get("critical_unauthorized"))

    denom = float(len(checked) or 1) if checked else 0.0
    metrics = [
        KpiMetric(
            id="accuracy",
            title="Точность результата",
            formula="confirmed_without_material_error / checked × 100%",
            value=_pct(confirmed, denom) if checked else None,
            target=TARGET_ACCURACY,
            target_label="≥ 95%",
            tone=_tone(_pct(confirmed, denom) if checked else None, TARGET_ACCURACY),
            sample_size=sample,
            details={"confirmed": confirmed, "checked": len(checked)},
        ),
        KpiMetric(
            id="completeness",
            title="Полнота",
            formula="filled_mandatory / required × 100%",
            value=_pct(complete, denom) if checked else None,
            target=TARGET_COMPLETENESS,
            target_label="≥ 98%",
            tone=_tone(_pct(complete, denom) if checked else None, TARGET_COMPLETENESS),
            sample_size=sample,
            details={"complete": complete, "checked": len(checked)},
        ),
        KpiMetric(
            id="timeliness",
            title="Своевременность",
            formula="prepared_in_SLA / completed × 100%",
            value=_pct(timely, denom) if checked else None,
            target=TARGET_TIMELINESS,
            target_label="≥ 95%",
            tone=_tone(_pct(timely, denom) if checked else None, TARGET_TIMELINESS),
            sample_size=sample,
            details={"timely": timely, "checked": len(checked)},
        ),
        KpiMetric(
            id="rework",
            title="Доля существенных доработок",
            formula="substantially_reworked / checked × 100%",
            value=_pct(reworked, denom) if checked else None,
            target=TARGET_REWORK_PILOT,
            target_label="≤ 10% (пилот)",
            unit="%",
            tone=_tone(
                _pct(reworked, denom) if checked else None,
                TARGET_REWORK_PILOT,
                higher_is_better=False,
            ),
            sample_size=sample,
            details={"reworked": reworked, "checked": len(checked)},
        ),
        KpiMetric(
            id="traceability",
            title="Прослеживаемость",
            formula="results_with_refs / all × 100%",
            value=_pct(traced, denom) if checked else None,
            target=TARGET_TRACEABILITY,
            target_label="100%",
            tone=_tone(_pct(traced, denom) if checked else None, TARGET_TRACEABILITY),
            sample_size=sample,
            details={"traced": traced, "checked": len(checked)},
        ),
        KpiMetric(
            id="critical",
            title="Критические / несанкционированные действия",
            formula="count",
            value=float(critical),
            target=TARGET_CRITICAL,
            target_label="0",
            unit="",
            tone=_tone(float(critical), TARGET_CRITICAL, higher_is_better=False),
            sample_size=sample,
            details={"critical": critical},
        ),
    ]
    return metrics


SPECIAL_KPI_SPECS: dict[str, list[dict[str, Any]]] = {
    "otk_head_agent": [
        {
            "id": "otk_assign_2wh",
            "title": "Назначение ≤ 2 раб. ч",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "assigned_within_2wh",
        },
        {
            "id": "otk_confirm_1wh",
            "title": "Подтверждение акта ≤ 1 раб. ч",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "act_confirmed_within_1wh",
        },
        {
            "id": "otk_zdk_1600",
            "title": "Акты к ЗДК до 16:00",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "handed_to_zdk_by_1600",
        },
        {
            "id": "otk_missed_critical",
            "title": "Пропущенные критические нарушения",
            "target": 0.0,
            "target_label": "0",
            "flag": "missed_critical",
            "higher_is_better": False,
            "as_count": True,
        },
    ],
    "quality_engineer_agent": [
        {
            "id": "qi_docs",
            "title": "Полнота обязательных docs",
            "target": 100.0,
            "target_label": "100%",
            "flag": "docs_complete",
        },
        {
            "id": "qi_program",
            "title": "Точность программы / выборки",
            "target": 100.0,
            "target_label": "100%",
            "flag": "program_ok",
        },
        {
            "id": "qi_results",
            "title": "Полнота записи результатов",
            "target": 100.0,
            "target_label": "100%",
            "flag": "results_complete",
        },
        {
            "id": "qi_false_release",
            "title": "Ложные разрешающие статусы",
            "target": 0.0,
            "target_label": "0",
            "flag": "false_releasing_status",
            "higher_is_better": False,
            "as_count": True,
        },
        {
            "id": "qi_timely_act",
            "title": "Своевременность акта / ярлыка",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "act_label_timely",
        },
    ],
    "quality_deputy_director_agent": [
        {
            "id": "zdk_8wh",
            "title": "Резолюция ≤ 8 раб. ч",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "resolution_within_8wh",
        },
        {
            "id": "zdk_allowlist",
            "title": "Резолюция в допустимом перечне",
            "target": 100.0,
            "target_label": "100%",
            "flag": "disposition_allowed",
        },
        {
            "id": "zdk_conditions",
            "title": "Полнота условий исполнения",
            "target": 100.0,
            "target_label": "100%",
            "flag": "conditions_complete",
        },
        {
            "id": "zdk_contradictory",
            "title": "Противоречивые / неисполнимые",
            "target": 0.0,
            "target_label": "0",
            "flag": "contradictory_resolution",
            "higher_is_better": False,
            "as_count": True,
        },
    ],
}


def compute_special_kpis(agent_id: str, tasks: list[dict[str, Any]]) -> list[KpiMetric]:
    specs = SPECIAL_KPI_SPECS.get(agent_id) or []
    checked = [t for t in tasks if t.get("checked")] or tasks
    metrics: list[KpiMetric] = []
    for spec in specs:
        flag = spec["flag"]
        as_count = bool(spec.get("as_count"))
        higher = bool(spec.get("higher_is_better", True))
        if as_count:
            value = float(sum(1 for t in checked if t.get(flag)))
        else:
            hits = sum(1 for t in checked if t.get(flag))
            value = _pct(hits, float(len(checked))) if checked else None
        metrics.append(
            KpiMetric(
                id=spec["id"],
                title=spec["title"],
                formula=flag,
                value=value,
                target=float(spec["target"]),
                target_label=str(spec["target_label"]),
                unit="" if as_count else "%",
                tone=_tone(value, float(spec["target"]), higher_is_better=higher),
                sample_size=len(checked),
            )
        )
    return metrics


def compute_system_quality_kpis(cases: list[dict[str, Any]]) -> list[KpiMetric]:
    """§12.3 quality-relevant system KPIs."""
    n = len(cases)
    sla_ok = sum(1 for c in cases if c.get("incoming_control_sla_met"))
    without_status = sum(1 for c in cases if c.get("available_without_releasing_status"))
    traced = sum(1 for c in cases if c.get("control_traceability_ok"))
    return [
        KpiMetric(
            id="incoming_control_sla",
            title="Соблюдение SLA входного контроля",
            formula="in_SLA / completed × 100%",
            value=_pct(sla_ok, float(n)) if n else None,
            target=TARGET_INCOMING_SLA,
            target_label="≥ 95%",
            tone=_tone(_pct(sla_ok, float(n)) if n else None, TARGET_INCOMING_SLA),
            sample_size=n,
        ),
        KpiMetric(
            id="lots_without_releasing_status",
            title="Партии без разрешающего статуса в доступном запасе",
            formula="count",
            value=float(without_status),
            target=TARGET_LOTS_WITHOUT_STATUS,
            target_label="0",
            unit="",
            tone=_tone(
                float(without_status),
                TARGET_LOTS_WITHOUT_STATUS,
                higher_is_better=False,
            ),
            sample_size=n,
        ),
        KpiMetric(
            id="control_traceability",
            title="Прослеживаемость результата контроля",
            formula="lots_with_full_refs / all × 100%",
            value=_pct(traced, float(n)) if n else None,
            target=TARGET_CONTROL_TRACEABILITY,
            target_label="100%",
            tone=_tone(
                _pct(traced, float(n)) if n else None, TARGET_CONTROL_TRACEABILITY
            ),
            sample_size=n,
        ),
    ]


__all__ = [
    "SPECIAL_KPI_SPECS",
    "compute_common_kpis",
    "compute_special_kpis",
    "compute_system_quality_kpis",
]
