"""Резолвер актуального помесячного плана производства на год."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import json
from typing import Any, Protocol


class _HeaderLike(Protocol):
    ref_key: str
    number: str
    plan_date: datetime | None
    period_start: datetime | None
    period_end: datetime | None
    raw_json: str


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
    source_refs: tuple[str, ...] = ()
    source_numbers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_key": self.ref_key,
            "number": self.number,
            "date": self.plan_date.isoformat() if self.plan_date else "",
            "period_start": self.period_start.isoformat() if self.period_start else "",
            "period_end": self.period_end.isoformat() if self.period_end else "",
            "source_count": len(self.source_refs) or (1 if self.ref_key else 0),
            "source_refs": list(self.source_refs or ((self.ref_key,) if self.ref_key else ())),
            "source_numbers": list(
                self.source_numbers or ((self.number,) if self.number else ())
            ),
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


def _month_source_from_headers(headers: list[_HeaderLike]) -> MonthPlanSource:
    ordered = sorted(headers, key=_header_sort_key, reverse=True)
    primary = ordered[0]
    source_refs = tuple(header.ref_key for header in ordered if header.ref_key)
    source_numbers = tuple(header.number for header in ordered if header.number)
    number = ", ".join(source_numbers[:3])
    if len(source_numbers) > 3:
        number = f"{number} и ещё {len(source_numbers) - 3}"
    return MonthPlanSource(
        ref_key=primary.ref_key,
        number=number or primary.number,
        plan_date=primary.plan_date,
        period_start=primary.period_start,
        period_end=primary.period_end,
        source_refs=source_refs,
        source_numbers=source_numbers,
    )


def _header_business_key(header: _HeaderLike) -> tuple[str, str, str, str]:
    """Срез плана 1С: сценарий + вид плана + подразделение + ответственный.

    В текущем месяце такие срезы из 1С надо суммировать между собой, но внутри одного
    среза более поздний документ заменяет старую редакцию.
    """
    raw_text = str(getattr(header, "raw_json", "") or "").strip()
    raw: dict[str, Any] = {}
    if raw_text:
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                raw = parsed
        except json.JSONDecodeError:
            raw = {}

    scenario = str(raw.get("scenario_key") or raw.get("Сценарий_Key") or "").strip()
    plan_type = str(raw.get("plan_type_key") or raw.get("ВидПлана_Key") or "").strip()
    department = str(
        raw.get("dispatcher_department_key")
        or raw.get("ПодразделениеДиспетчер_Key")
        or raw.get("Подразделение_Key")
        or ""
    ).strip()
    responsible = str(raw.get("responsible_key") or raw.get("Ответственный_Key") or "").strip()
    if scenario or plan_type or department or responsible:
        return scenario, plan_type, department, responsible
    return ("ref", header.ref_key, "", "")


def _latest_headers_by_business_key(headers: list[_HeaderLike]) -> list[_HeaderLike]:
    latest: dict[tuple[str, str, str, str], _HeaderLike] = {}
    for header in headers:
        key = _header_business_key(header)
        previous = latest.get(key)
        if previous is None or _header_sort_key(header) > _header_sort_key(previous):
            latest[key] = header
    return sorted(latest.values(), key=_header_sort_key, reverse=True)


def resolve_year_production_plan(
    headers: list[_HeaderLike],
    items: list[_ItemLike],
    *,
    year: int | None = None,
    merge_month_keys: set[str] | None = None,
) -> ResolvedYearProductionPlan:
    """Резолвит годовой план.

    По умолчанию для месяца берётся последний документ, как в старой логике. Для месяцев
    из merge_month_keys строки всех актуальных документов складываются в единый план.
    Это нужно для текущего месяца 1С, где разные площадки/виды плана лежат отдельными
    документами и вместе образуют реальный закупочный спрос месяца.
    """
    target_year = year or date.today().year
    merge_month_keys = set(merge_month_keys or set())
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

        if month_key in merge_month_keys:
            candidates_with_items = [
                header
                for header in candidates
                if items_by_plan_month.get((header.ref_key, month_key), [])
            ]
            selected = [
                header
                for header in _latest_headers_by_business_key(candidates_with_items)
            ]
        else:
            selected = [max(candidates, key=_header_sort_key)]

        month_items: list[_ItemLike] = []
        for header in selected:
            month_items.extend(items_by_plan_month.get((header.ref_key, month_key), []))
        if not month_items:
            resolved.gaps.append(month_key)
            continue

        resolved.month_sources[month_key] = _month_source_from_headers(selected)
        resolved.rows.extend(month_items)

    return resolved
