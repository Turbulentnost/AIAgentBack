"""KPI formula unit tests (§12 / СТО tone rules)."""

from __future__ import annotations

from app.agents.quality_kpi_agent.formulas import (
    SPECIAL_KPI_SPECS,
    compute_common_kpis,
    compute_special_kpis,
    compute_system_quality_kpis,
)
from app.agents.procurement_role_agents.config import KPI_EVALUATED_AGENT_IDS


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
    assert metrics["critical"].tone == "ok"


def test_common_kpis_empty_sample_is_unknown_not_ok() -> None:
    metrics = {m.id: m for m in compute_common_kpis([])}
    assert all(m.tone == "unknown" for m in metrics.values())
    assert all(m.value is None for m in metrics.values())
    assert metrics["critical"].value is None


def test_common_kpis_warn_band() -> None:
    # 91% accuracy vs 95% → warn (within 5 pp)
    tasks = [
        {
            "checked": True,
            "confirmed_without_material_error": i < 91,
            "completeness_ok": True,
            "sla_met": True,
            "substantially_reworked": False,
            "traceability_ok": True,
            "critical_unauthorized": False,
        }
        for i in range(100)
    ]
    metrics = {m.id: m for m in compute_common_kpis(tasks)}
    assert metrics["accuracy"].value == 91.0
    assert metrics["accuracy"].tone == "warn"


def test_common_kpis_bad_outside_band() -> None:
    tasks = [
        {
            "checked": True,
            "confirmed_without_material_error": i < 80,
            "completeness_ok": True,
            "sla_met": True,
            "substantially_reworked": False,
            "traceability_ok": True,
            "critical_unauthorized": False,
        }
        for i in range(100)
    ]
    metrics = {m.id: m for m in compute_common_kpis(tasks)}
    assert metrics["accuracy"].tone == "bad"


def test_strict_target_any_miss_is_bad() -> None:
    tasks = [
        {
            "checked": True,
            "confirmed_without_material_error": True,
            "completeness_ok": True,
            "sla_met": True,
            "substantially_reworked": False,
            "traceability_ok": i > 0,  # 99/100
            "critical_unauthorized": False,
        }
        for i in range(100)
    ]
    metrics = {m.id: m for m in compute_common_kpis(tasks)}
    assert metrics["traceability"].value == 99.0
    assert metrics["traceability"].tone == "bad"


def test_special_otk_kpis() -> None:
    tasks = [
        {
            "checked": True,
            "assigned_within_2wh": True,
            "act_confirmed_within_1wh": False,
            "handed_to_zdk_by_1600": True,
            "missed_critical": False,
            "quarterly_report_complete": True,
        }
    ]
    metrics = {m.id: m for m in compute_special_kpis("otk_head_agent", tasks)}
    assert metrics["otk_assign_2wh"].value == 100.0
    assert metrics["otk_confirm_1wh"].value == 0.0
    assert metrics["otk_confirm_1wh"].tone == "bad"
    assert metrics["otk_quarterly_report"].tone == "ok"


def test_special_empty_is_unknown() -> None:
    metrics = compute_special_kpis("otk_head_agent", [])
    assert metrics
    assert all(m.tone == "unknown" for m in metrics)


def test_system_kpis() -> None:
    cases = [
        {
            "incoming_control_sla_met": True,
            "available_without_releasing_status": False,
            "control_traceability_ok": True,
            "mandatory_data_ok": True,
            "purchase_without_basis": False,
            "procurement_sla_met": True,
            "receipt_sla_met": True,
            "hx_action_by_ai": False,
        },
        {
            "incoming_control_sla_met": False,
            "available_without_releasing_status": True,
            "control_traceability_ok": True,
            "mandatory_data_ok": True,
            "purchase_without_basis": False,
            "procurement_sla_met": False,
            "receipt_sla_met": True,
            "hx_action_by_ai": False,
        },
    ]
    metrics = {m.id: m for m in compute_system_quality_kpis(cases)}
    assert metrics["incoming_control_sla"].value == 50.0
    assert metrics["incoming_control_sla"].tone == "bad"
    assert metrics["lots_without_releasing_status"].value == 1.0
    assert metrics["lots_without_releasing_status"].tone == "bad"
    assert metrics["control_traceability"].value == 100.0


def test_system_kpis_empty_unknown() -> None:
    metrics = compute_system_quality_kpis([])
    assert all(m.tone == "unknown" for m in metrics)


def test_evaluated_agents_have_special_specs() -> None:
    for agent_id in KPI_EVALUATED_AGENT_IDS:
        assert agent_id in SPECIAL_KPI_SPECS, agent_id
        assert SPECIAL_KPI_SPECS[agent_id], agent_id
