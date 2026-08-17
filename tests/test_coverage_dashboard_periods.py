"""Unit-тесты пересчёта дашборда обеспеченности по периодам."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.agents.document_analysis_agent.coverage_dashboard import (
    build_coverage_dashboard,
    build_coverage_period_for_range,
    coverage_period_from_snapshot,
    dump_coverage_rebuild,
    rebuild_coverage_period_from_cache,
)
from app.agents.document_analysis_agent.product_coverage import compute_daily_plan_coverage


def _plan(product: str, daily: dict[str, float], daily_fact: dict[str, float] | None = None):
    return SimpleNamespace(
        product=product,
        daily_qty=daily,
        daily_fact=daily_fact or {},
    )


def _row(name: str, by_product: dict[str, float], stock: float = 0.0):
    return SimpleNamespace(
        nomenclature=name,
        by_product=by_product,
        stock=stock,
        daily_receipts={},
        monthly_receipts={},
    )


def test_coverage_periods_recalculate_plan_and_tiles():
    day_keys = [f"2026-08-{day:02d}" for day in range(1, 32)]
    plans = [
        _plan("A", {"2026-08-10": 10, "2026-08-11": 20, "2026-08-12": 30}, {"2026-08-10": 5}),
    ]
    merged = [_row("M", {"A": 1}, stock=15)]
    daily = compute_daily_plan_coverage(plans, merged, day_keys)

    payload = build_coverage_dashboard(
        daily_plan_coverage=daily,
        product_coverage=None,
        merged=merged,
        day_keys=day_keys,
        detailed_plans=plans,
        as_of=date(2026, 8, 10),
        schedule_month="2026-08",
    )
    assert payload is not None

    day = payload["periods"]["day"]["products"]["tiles"]
    week = payload["periods"]["week"]["products"]["tiles"]
    month = payload["periods"]["month"]["products"]["tiles"]

    assert day["plan_total"] == 10
    assert week["plan_total"] == 60
    assert month["plan_total"] == 60
    assert day["fact_total"] == 5
    assert week["fact_total"] == 5
    assert day["plan_total"] != week["plan_total"]
    assert day["green_plan_total"] == 10
    assert day["green_covered_total"] == 10
    assert week["yellow_plan_total"] == 60
    assert week["yellow_covered_total"] == 15


def test_coverage_period_for_custom_date_range():
    day_keys = [f"2026-08-{day:02d}" for day in range(10, 16)]
    plans = [_plan("A", {day: 10 for day in day_keys})]
    merged = [_row("M", {"A": 1}, stock=100)]
    daily = compute_daily_plan_coverage(plans, merged, day_keys)

    payload = build_coverage_period_for_range(
        daily_plan_coverage=daily,
        product_coverage=None,
        merged=merged,
        day_keys=day_keys,
        detailed_plans=plans,
        as_of=date(2026, 8, 10),
        schedule_month="2026-08",
        date_from=date(2026, 8, 11),
        date_to=date(2026, 8, 13),
    )
    assert payload is not None
    assert payload["days"] == ["2026-08-11", "2026-08-12", "2026-08-13"]
    assert payload["products"]["tiles"]["plan_total"] == 30


def test_coverage_period_rebuilds_from_analysis_cache():
    day_keys = [f"2026-08-{day:02d}" for day in range(10, 16)]
    plans = [_plan("A", {day: 10 for day in day_keys})]
    merged = [_row("M", {"A": 1}, stock=100)]
    cache = dump_coverage_rebuild(
        merged=merged,
        detailed_plans=plans,
        day_keys=day_keys,
        as_of=date(2026, 8, 10),
        schedule_month="2026-08",
    )
    payload = rebuild_coverage_period_from_cache(
        cache,
        date(2026, 8, 11),
        date(2026, 8, 13),
    )
    assert payload is not None
    assert payload["days"] == ["2026-08-11", "2026-08-12", "2026-08-13"]
    assert payload["products"]["tiles"]["plan_total"] == 30


def test_coverage_period_matches_snapshot_week_without_cache():
    snapshot = {
        "coverage_dashboard": {
            "periods": {
                "week": {
                    "key": "week",
                    "days": ["2026-08-17", "2026-08-23"],
                    "products": {"tiles": {"all": 4}, "rows": []},
                    "nomenclatures": {"tiles": {"all": 2}, "rows": []},
                }
            }
        }
    }
    payload = coverage_period_from_snapshot(
        snapshot,
        date(2026, 8, 17),
        date(2026, 8, 23),
    )
    assert payload is not None
    assert payload["days"] == ["2026-08-17", "2026-08-23"]
    assert payload["products"]["tiles"]["all"] == 4


def test_nomenclature_period_uses_rolling_stock():
    day_keys = ["2026-08-10", "2026-08-11"]
    row = SimpleNamespace(
        nomenclature="Болт",
        by_product={},
        stock=15.0,
        daily_demand={"2026-08-10": 10.0, "2026-08-11": 10.0},
        daily_demand_fact={"2026-08-10": 0.0, "2026-08-11": 0.0},
        daily_receipts={},
        monthly_demand={},
        monthly_receipts={},
    )
    payload = build_coverage_dashboard(
        daily_plan_coverage=None,
        product_coverage=None,
        merged=[row],
        day_keys=day_keys,
        detailed_plans=[],
        as_of=date(2026, 8, 10),
        schedule_month="2026-08",
    )
    assert payload is not None
    day_tiles = payload["periods"]["day"]["nomenclatures"]["tiles"]
    week_tiles = payload["periods"]["week"]["nomenclatures"]["tiles"]
    assert day_tiles["plan_total"] == 10
    assert week_tiles["plan_total"] == 20
    assert day_tiles["covered_total"] == 10
    assert week_tiles["covered_total"] == 15
    assert day_tiles["green_plan_total"] == 10
    assert week_tiles["yellow_plan_total"] == 20
    assert week_tiles["yellow_covered_total"] == 15


def test_product_shortages_attached_for_unprovided():
    day_keys = ["2026-08-10", "2026-08-11"]
    plans = [_plan("A", {"2026-08-10": 10, "2026-08-11": 10})]
    merged = [
        SimpleNamespace(
            nomenclature="Болт M8",
            by_product={"A": 2.0},
            stock=5.0,
            daily_demand={"2026-08-10": 0.0, "2026-08-11": 0.0},
            daily_demand_fact={},
            daily_receipts={},
            monthly_demand={},
            monthly_receipts={},
        )
    ]
    daily = compute_daily_plan_coverage(plans, merged, day_keys)
    payload = build_coverage_dashboard(
        daily_plan_coverage=daily,
        product_coverage=None,
        merged=merged,
        day_keys=day_keys,
        detailed_plans=plans,
        as_of=date(2026, 8, 10),
        schedule_month="2026-08",
    )
    assert payload is not None
    rows = payload["periods"]["week"]["products"]["rows"]
    product = next(row for row in rows if row["name"] == "A")
    assert product["status"] in ("red", "yellow")
    assert product["shortages"]
    shortage = product["shortages"][0]
    assert "M8" in shortage["name"] or shortage["name"] == "Болт M8"
    assert shortage["plan"] == 40.0
    assert shortage["stock"] == 5.0
    assert shortage["expected"] == 0.0
    assert shortage["shortage"] == 35.0
