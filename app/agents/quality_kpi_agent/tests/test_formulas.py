"""KPI formula unit tests (§12)."""

from __future__ import annotations

from app.agents.quality_kpi_agent.formulas import (
    compute_common_kpis,
    compute_special_kpis,
    compute_system_quality_kpis,
)


def test_common_kpis_perfect() -> None:
    tasks = [
        {
            "checked": True,
            "confirmed_without_material_error": True,
            "completeness_ok": True,
            "sla_met": True,
            "substantially_reworked": False,
            "traceability_ok": True,
            "critical_unauthorized": False,
        }
        for _ in range(10)
    ]
    metrics = {m.id: m for m in compute_common_kpis(tasks)}
    assert metrics["accuracy"].value == 100.0
    assert metrics["accuracy"].tone == "ok"
    assert metrics["rework"].value == 0.0
    assert metrics["critical"].value == 0.0


def test_special_otk_kpis() -> None:
    tasks = [
        {
            "checked": True,
            "assigned_within_2wh": True,
            "act_confirmed_within_1wh": False,
            "handed_to_zdk_by_1600": True,
            "missed_critical": False,
        }
    ]
    metrics = {m.id: m for m in compute_special_kpis("otk_head_agent", tasks)}
    assert metrics["otk_assign_2wh"].value == 100.0
    assert metrics["otk_confirm_1wh"].value == 0.0
    assert metrics["otk_confirm_1wh"].tone == "bad"


def test_system_kpis() -> None:
    cases = [
        {
            "incoming_control_sla_met": True,
            "available_without_releasing_status": False,
            "control_traceability_ok": True,
        },
        {
            "incoming_control_sla_met": False,
            "available_without_releasing_status": True,
            "control_traceability_ok": True,
        },
    ]
    metrics = {m.id: m for m in compute_system_quality_kpis(cases)}
    assert metrics["incoming_control_sla"].value == 50.0
    assert metrics["lots_without_releasing_status"].value == 1.0
    assert metrics["lots_without_releasing_status"].tone in {"warn", "bad"}
    assert metrics["control_traceability"].value == 100.0
