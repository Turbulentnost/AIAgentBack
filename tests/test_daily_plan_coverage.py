"""Unit-тесты дневной обеспеченности плана П/ф (rolling + fair share)."""

from __future__ import annotations

from types import SimpleNamespace

from app.agents.document_analysis_agent.product_coverage import (
    compute_daily_plan_coverage,
)


def _plan(product: str, daily: dict[str, float]):
    return SimpleNamespace(product=product, daily_qty=daily, daily_fact={})


def _row(
    name: str,
    by_product: dict[str, float],
    stock: float = 0.0,
    daily_receipts: dict | None = None,
):
    return SimpleNamespace(
        nomenclature=name,
        by_product=by_product,
        stock=stock,
        daily_receipts=daily_receipts or {},
        monthly_receipts={},
    )


def test_daily_shared_material_fair_split():
    days = ["2026-07-01", "2026-07-02"]
    plans = [
        _plan("FPV СОКОЛ И день", {"2026-07-01": 100, "2026-07-02": 100}),
        _plan("FPV СОКОЛ Т ночь", {"2026-07-01": 50, "2026-07-02": 50}),
    ]
    merged = [
        _row(
            "KIT",
            {"FPV СОКОЛ И день": 1, "FPV СОКОЛ Т ночь": 1},
            stock=150,
        )
    ]
    result = compute_daily_plan_coverage(plans, merged, days)
    d1_a = result.cell("FPV СОКОЛ И день", "2026-07-01")
    d1_b = result.cell("FPV СОКОЛ Т ночь", "2026-07-01")
    assert d1_a.covered + d1_b.covered == 150
    assert d1_a.covered == 100
    assert d1_b.covered == 50
    # day2: stock consumed → 0 left
    assert result.cell("FPV СОКОЛ И день", "2026-07-02").covered == 0
    assert result.status_for_plan_cell("FPV СОКОЛ И день", ["2026-07-01"], 100) == "green"
    assert result.status_for_plan_cell("FPV СОКОЛ И день", ["2026-07-02"], 100) == "red"


def test_daily_receipt_unlocks_second_day():
    days = ["2026-07-01", "2026-07-02"]
    plans = [_plan("A", {"2026-07-01": 10, "2026-07-02": 10})]
    merged = [_row("M", {"A": 1}, stock=10, daily_receipts={"2026-07-02": 5})]
    result = compute_daily_plan_coverage(plans, merged, days)
    assert result.cell("A", "2026-07-01").covered == 10
    assert result.cell("A", "2026-07-02").covered == 5
    assert result.status_for_plan_cell("A", ["2026-07-02"], 10) == "yellow"


def test_daily_coverage_keeps_detailed_fact():
    days = ["2026-07-01", "2026-07-02"]
    plans = [
        SimpleNamespace(
            product="A",
            daily_qty={"2026-07-01": 10, "2026-07-02": 10},
            daily_fact={"2026-07-01": 7, "2026-07-02": 4},
        )
    ]
    merged = [_row("M", {"A": 1}, stock=20)]

    result = compute_daily_plan_coverage(plans, merged, days)

    assert result.cell("A", "2026-07-01").fact == 7
    assert result.cell("A", "2026-07-02").fact == 4


def test_unmatched_bom_is_red():
    days = ["2026-07-01"]
    plans = [_plan("Неизвестное", {"2026-07-01": 5})]
    merged = [_row("M", {"Другое": 1}, stock=100)]
    result = compute_daily_plan_coverage(plans, merged, days)
    assert result.cell("Неизвестное", "2026-07-01").covered == 0
    assert result.status_for_plan_cell("Неизвестное", ["2026-07-01"], 5) == "red"


def test_detailed_short_name_aliases():
    from app.agents.document_analysis_agent.product_coverage import _match_detailed_to_catalog

    catalog = [
        'FPV-перехватчик "СОКОЛ" И (день)',
        'FPV-перехватчик "СОКОЛ" Т (ночь)',
        "FPV-перехватчик СОКОЛ ИС (ДЕНЬ)",
        "FPV-перехватчик СОКОЛ ИС -Т (НОЧЬ)",
    ]
    assert _match_detailed_to_catalog("Сокол И", catalog) == catalog[0]
    assert _match_detailed_to_catalog("Сокол ИТ", catalog) == catalog[1]
    assert _match_detailed_to_catalog("Сокол ИС", catalog) == catalog[2]
    assert _match_detailed_to_catalog("Сокол ИСТ", catalog) == catalog[3]


def test_detailed_z40_matches_by_match_key():
    from app.agents.document_analysis_agent.product_coverage import compute_daily_plan_coverage

    schedule_name = 'FPV-перехватчик "Сокол" Р (Z40)'
    detailed_name = 'FPV-перехватчик "СОКОЛ" Р (Z40)'
    merged = [
        _row("Корпус Z40", {schedule_name: 1}, stock=100),
    ]
    plans = [_plan(detailed_name, {"2026-08-13": 10})]
    result = compute_daily_plan_coverage(plans, merged, ["2026-08-13"])
    assert result.boms[detailed_name].matched is True
    assert result.cell(detailed_name, "2026-08-13").covered == 10

