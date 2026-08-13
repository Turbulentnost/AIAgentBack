"""Резолвер актуального помесячного плана производства на год."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Protocol


class _HeaderLike(Protocol):
    ref_key: str
    number: str
    plan_date: datetime | None
    period_start: datetime | None
    period_end: datetime | None


class _ItemLike(Protocol):
    plan_ref_key: str
    month_key: str
    line_number: int
    product_date: datetime | None
    nomenclature_key: str
    nomenclature_code: str
    nomenclature_name: str
    qty: float
    unit: str


@dataclass(frozen=True)
class MonthPlanSource:
    ref_key: str
    number: str
    plan_date: datetime | None
    period_start: datetime | None
    period_end: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_key": self.ref_key,
            "number": self.number,
            "date": self.plan_date.isoformat() if self.plan_date else "",
            "period_start": self.period_start.isoformat() if self.period_start else "",
            "period_end": self.period_end.isoformat() if self.period_end else "",
        }


@dataclass
class ResolvedYearProductionPlan:
    year: int
    rows: list[Any] = field(default_factory=list)
    month_sources: dict[str, MonthPlanSource] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)

    def to_meta(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "month_sources": {key: source.to_dict() for key, source in self.month_sources.items()},
            "gaps": self.gaps,
        }


def year_month_keys(year: int) -> list[str]:
    return [f"{year:04d}-{month:02d}" for month in range(1, 13)]


def _month_bounds(month_key: str) -> tuple[date, date] | None:
    if not month_key or len(month_key) < 7 or "-" not in month_key:
        return None
    try:
        year_s, month_s = month_key.split("-", 1)
        year = int(year_s)
        month = int(month_s)
        if year < 1900 or month < 1 or month > 12:
            return None
        last_day = monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last_day)
    except ValueError:
        return None


def header_covers_month(header: _HeaderLike, month_key: str) -> bool:
    bounds = _month_bounds(month_key)
    if bounds is None:
        return False
    month_start, month_end = bounds
    period_start = header.period_start.date() if header.period_start else None
    period_end = header.period_end.date() if header.period_end else None
    if period_start is not None and period_end is not None:
        return period_start <= month_end and period_end >= month_start
    if header.plan_date is not None:
        return header.plan_date.year == month_start.year and header.plan_date.month == month_start.month
    return False


def _header_sort_key(header: _HeaderLike) -> datetime:
    if header.plan_date is not None:
        dt = header.plan_date
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def resolve_year_production_plan(
    headers: list[_HeaderLike],
    items: list[_ItemLike],
    *,
    year: int | None = None,
) -> ResolvedYearProductionPlan:
    """Для каждого месяца года выбирает последний документ, покрывающий месяц."""
    target_year = year or date.today().year
    items_by_plan_month: dict[tuple[str, str], list[_ItemLike]] = {}
    for item in items:
        month_key = (item.month_key or "").strip()
        if not month_key:
            continue
        items_by_plan_month.setdefault((item.plan_ref_key, month_key), []).append(item)

    resolved = ResolvedYearProductionPlan(year=target_year)
    for month_key in year_month_keys(target_year):
        candidates = [header for header in headers if header_covers_month(header, month_key)]
        if not candidates:
            doc_refs_with_items = {
                plan_ref_key
                for (plan_ref_key, item_month), rows in items_by_plan_month.items()
                if item_month == month_key and rows
            }
            if not doc_refs_with_items:
                resolved.gaps.append(month_key)
                continue
            candidates = [header for header in headers if header.ref_key in doc_refs_with_items]

        winner = max(candidates, key=_header_sort_key)
        month_items = items_by_plan_month.get((winner.ref_key, month_key), [])
        if not month_items:
            resolved.gaps.append(month_key)
            continue

        resolved.month_sources[month_key] = MonthPlanSource(
            ref_key=winner.ref_key,
            number=winner.number,
            plan_date=winner.plan_date,
            period_start=winner.period_start,
            period_end=winner.period_end,
        )
        resolved.rows.extend(month_items)

    return resolved
