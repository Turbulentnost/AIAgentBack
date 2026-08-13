from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.onec_production_plan_resolver import resolve_year_production_plan


@dataclass
class _Header:
    ref_key: str
    number: str
    plan_date: datetime | None
    period_start: datetime | None
    period_end: datetime | None


@dataclass
class _Item:
    plan_ref_key: str
    month_key: str
    line_number: int
    product_date: datetime | None
    nomenclature_key: str
    nomenclature_code: str
    nomenclature_name: str
    qty: float
    unit: str


def test_resolve_year_picks_latest_document_for_overlapping_month() -> None:
    old_doc = _Header(
        ref_key="old",
        number="OLD-1",
        plan_date=datetime(2026, 6, 15, tzinfo=timezone.utc),
        period_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    new_doc = _Header(
        ref_key="new",
        number="NEW-1",
        plan_date=datetime(2026, 8, 12, tzinfo=timezone.utc),
        period_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )
    items = [
        _Item("old", "2026-07", 1, None, "a", "001", "A", 100, "шт"),
        _Item("old", "2026-08", 2, None, "a", "001", "A", 200, "шт"),
        _Item("new", "2026-09", 1, None, "a", "001", "A", 300, "шт"),
        _Item("new", "2026-10", 2, None, "a", "001", "A", 400, "шт"),
    ]

    resolved = resolve_year_production_plan([old_doc, new_doc], items, year=2026)

    assert "2026-07" in resolved.month_sources
    assert resolved.month_sources["2026-07"].ref_key == "old"
    assert "2026-08" in resolved.month_sources
    assert resolved.month_sources["2026-08"].ref_key == "old"
    assert "2026-09" in resolved.month_sources
    assert resolved.month_sources["2026-09"].ref_key == "new"
    assert "2026-01" in resolved.gaps
    assert len(resolved.rows) == 4


def test_resolve_year_uses_items_when_period_missing() -> None:
    header = _Header(
        ref_key="doc",
        number="DOC-1",
        plan_date=datetime(2026, 8, 12, tzinfo=timezone.utc),
        period_start=None,
        period_end=None,
    )
    items = [_Item("doc", "2026-09", 1, None, "a", "001", "A", 50, "шт")]

    resolved = resolve_year_production_plan([header], items, year=2026)

    assert resolved.month_sources["2026-09"].ref_key == "doc"
    assert resolved.rows[0].qty == 50
