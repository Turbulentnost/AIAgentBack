"""Unit-тесты дашборда обеспеченности."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.agents.document_analysis_agent.coverage_dashboard import build_coverage_dashboard
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
