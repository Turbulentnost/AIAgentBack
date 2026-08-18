"""Unit-тесты дашборда обеспеченности."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.agents.document_analysis_agent.coverage_dashboard import (
    _tile_stats,
    build_coverage_dashboard,
)
from app.agents.document_analysis_agent.excel_service import (
    DetailedScheduleExtract,
    MergedNomenclatureRow,
    ScheduleProductPlan,
    StockEntry,
    _replace_fact_demand_with_period_opening_stock,
)
from app.agents.document_analysis_agent.product_coverage import (
    ProductCoverageResult,
    ProductMonthCoverage,
)


def _merged_row(name: str, month: str, plan: float, stock: float = 0.0):
    return SimpleNamespace(
        nomenclature=name,
        daily_demand={},
        daily_demand_fact={},
        daily_receipts={},
        stock=stock,
        monthly_demand={
            month: {
                "заказ": {"план": plan, "факт": 0.0},
                "опытные": {"план": 0.0, "факт": 0.0},
                "склад": {"план": 0.0, "факт": 0.0},
            }
        },
        monthly_receipts={month: 0.0},
    )


def test_coverage_dashboard_uses_monthly_when_daily_demand_empty():
    month = "Август"
    merged = [_merged_row("Болт M3", month, plan=100.0, stock=40.0)]
    product_coverage = ProductCoverageResult(
        months=[month],
        products_in_order=["Изделие А"],
        boms={},
        cells={
            ("Изделие А", month): ProductMonthCoverage(
                product="Изделие А",
                month=month,
                plan=50.0,
                fact=0.0,
                covered=30.0,
            )
        },
    )
    payload = build_coverage_dashboard(
        daily_plan_coverage=None,
        product_coverage=product_coverage,
        merged=merged,
        day_keys=["2026-08-01", "2026-08-02"],
        as_of=date(2026, 8, 10),
        schedule_month="2026-08",
    )
    assert payload is not None
    week = payload["periods"]["week"]
    assert week["products"]["tiles"]["all"] == 1
    assert week["nomenclatures"]["tiles"]["all"] == 1
    assert week["nomenclatures"]["tiles"]["yellow"] == 1


def test_tile_stats_partition_plan_by_status_buckets():
    rows = [
        {"name": "A", "plan": 100.0, "covered": 100.0, "status": "green"},
        {"name": "B", "plan": 600.0, "covered": 286.0, "status": "yellow"},
        {"name": "C", "plan": 1400.0, "covered": 0.0, "status": "red"},
    ]
    tiles = _tile_stats(rows)
    assert tiles["plan_total"] == 2100.0
    assert tiles["green_plan_total"] == 100.0
    assert tiles["yellow_covered_total"] == 286.0
    assert tiles["shortfall_total"] == 1714.0
    assert tiles["shortfall_count"] == 2
    assert (
        tiles["green_plan_total"]
        + tiles["yellow_covered_total"]
        + tiles["shortfall_total"]
        == tiles["plan_total"]
    )


def test_fact_demand_replaced_with_opening_stock_snapshot():
    row = MergedNomenclatureRow(
        nomenclature="Болт M3",
        products=["Изделие А"],
        quantity=1.0,
        by_product={"Изделие А": 1.0},
        monthly_demand={
            "Январь": {
                "заказ": {"план": 10.0, "факт": 8.0},
                "опытные": {"план": 0.0, "факт": 0.0},
                "склад": {"план": 0.0, "факт": 0.0},
            }
        },
        daily_demand={"2020-01-01": 10.0, "2030-01-01": 10.0},
        daily_demand_fact={"2020-01-01": 8.0, "2030-01-01": 8.0},
    )
    snapshots = {
        "2020-01-01": {
            "болт m3": StockEntry(nomenclature="Болт M3", quantity=42.0),
        }
    }
    _replace_fact_demand_with_period_opening_stock(
        [row],
        [
            ScheduleProductPlan(
                product="Изделие А",
                monthly_qty={"Январь": {"заказ": {"план": 10.0, "факт": 0.0}}},
            )
        ],
        DetailedScheduleExtract(
            files=[],
            plans=[],
            year=2020,
            month=1,
            day_keys=["2020-01-01", "2030-01-01"],
        ),
        snapshots,
    )
    assert row.monthly_demand["Январь"]["_stock"]["факт"] == 42.0
    assert row.monthly_demand["Январь"]["заказ"]["факт"] == 0.0
    assert row.daily_demand_fact["2020-01-01"] == 42.0
    assert row.daily_demand_fact["2030-01-01"] == 0.0
