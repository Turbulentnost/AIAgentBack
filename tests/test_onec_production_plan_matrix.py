from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.onec_production_plan_matrix import (
    build_month_matrix,
    build_production_plan_matrices,
)


@dataclass
class _Row:
    month_key: str
    line_number: int
    product_date: datetime | None
    nomenclature_key: str
    nomenclature_code: str
    nomenclature_name: str
    qty: float
    unit: str


def test_month_matrix_day_granularity() -> None:
    rows = [
        _Row(
            month_key="2026-08",
            line_number=1,
            product_date=datetime(2026, 8, 5, tzinfo=timezone.utc),
            nomenclature_key="a",
            nomenclature_code="001",
            nomenclature_name="Изделие A",
            qty=10,
            unit="шт",
        ),
        _Row(
            month_key="2026-08",
            line_number=2,
            product_date=datetime(2026, 8, 5, tzinfo=timezone.utc),
            nomenclature_key="a",
            nomenclature_code="001",
            nomenclature_name="Изделие A",
            qty=5,
            unit="шт",
        ),
    ]
    matrix = build_month_matrix("2026-08", rows)
    assert matrix.granularity == "day"
    assert matrix.date_keys[0] == "2026-08-01"
    assert matrix.date_keys[-1] == "2026-08-31"
    assert matrix.products[0].qty_by_date["2026-08-05"] == 15


def test_month_matrix_month_only_granularity() -> None:
    rows = [
        _Row(
            month_key="2026-08",
            line_number=1,
            product_date=None,
            nomenclature_key="b",
            nomenclature_code="002",
            nomenclature_name="Изделие B",
            qty=100,
            unit="шт",
        ),
    ]
    matrix = build_month_matrix("2026-08", rows)
    assert matrix.granularity == "month"
    assert matrix.date_keys == ["2026-08"]
    assert matrix.products[0].month_only_qty == 100
