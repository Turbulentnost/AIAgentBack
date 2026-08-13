"""Сериализация дашборда обеспеченности для UI руководителя."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable

from app.agents.document_analysis_agent.product_coverage import (
    DailyPlanCoverageResult,
    ProductCoverageResult,
    _normalize,
    compute_daily_plan_coverage,
)

_MONTH_ORDER = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


def _month_label_from_iso(schedule_month: str) -> str | None:
    if len(schedule_month) != 7 or schedule_month[4] != "-":
        return None
    try:
        month_num = int(schedule_month[5:7])
    except ValueError:
        return None
    if month_num < 1 or month_num > 12:
        return None
    return _MONTH_ORDER[month_num - 1]


def _month_label_from_day_keys(day_keys: list[str]) -> str | None:
    for day_key in day_keys:
        try:
            return _MONTH_ORDER[date.fromisoformat(day_key).month - 1]
        except ValueError:
            continue
    return None


def _month_label_from_merged(merged: Iterable[Any]) -> str | None:
    for row in merged:
        monthly_demand = getattr(row, "monthly_demand", None) or {}
        if not monthly_demand:
            continue
        for month in _MONTH_ORDER:
            if month in monthly_demand:
                return month
        first = next(iter(monthly_demand.keys()), None)
        if isinstance(first, str) and first.strip():
            return first.strip()
    return None


def _resolve_month_label(
    *,
    schedule_month: str,
    day_keys: list[str],
    product_coverage: ProductCoverageResult | None,
    merged: Iterable[Any],
) -> str | None:
    label = _month_label_from_iso(schedule_month)
    if label:
        return label
    label = _month_label_from_day_keys(day_keys)
    if label:
        return label
    if product_coverage and product_coverage.months:
        return product_coverage.months[0]
    return _month_label_from_merged(merged)


def _period_day_keys(all_day_keys: list[str], period: str, as_of: date) -> list[str]:
    """Дни детального графика, попадающие в выбранный период UI."""
    if not all_day_keys:
        return []
    if period == "month":
        return list(all_day_keys)
    as_of_iso = as_of.isoformat()
    if period == "day":
        if as_of_iso in all_day_keys:
            return [as_of_iso]
        future = [day for day in all_day_keys if day >= as_of_iso]
        if future:
            return [future[0]]
        return [all_day_keys[-1]]
    monday = as_of - timedelta(days=as_of.weekday())
    sunday = monday + timedelta(days=6)
    start_iso = monday.isoformat()
    end_iso = sunday.isoformat()
    selected = [day for day in all_day_keys if start_iso <= day <= end_iso]
    if selected:
        return selected
    return list(all_day_keys)


def _daily_result_for_period(
    *,
    daily: DailyPlanCoverageResult | None,
    detailed_plans: list[Any] | None,
    merged: list[Any],
    all_day_keys: list[str],
    period_days: list[str],
) -> DailyPlanCoverageResult | None:
    """Дневная обеспеченность с накоплением остатков до конца периода."""
    if not period_days:
        return daily
    last_period_day = period_days[-1]
    sim_days = [day for day in all_day_keys if day <= last_period_day]
    if not sim_days:
        sim_days = list(period_days)

    if daily is not None and list(daily.day_keys) == sim_days:
        return daily

    if detailed_plans:
        return compute_daily_plan_coverage(detailed_plans, merged, sim_days)

    if daily is not None and set(sim_days).issubset(set(daily.day_keys)):
        return daily

    return daily


def _merged_by_norm_key(merged: Iterable[Any]) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for row in merged:
        key = _normalize(getattr(row, "nomenclature", ""))
        if key:
            index[key] = row
    return index


def _material_period_supply(
    merged_by_key: dict[str, Any],
    mat_key: str,
    period_days: list[str],
    all_day_keys: list[str],
) -> tuple[float, float]:
    """Остаток на начало периода и сумма ожидаемых поступлений за период."""
    row = merged_by_key.get(mat_key)
    if row is None:
        return 0.0, 0.0

    days_before = [day for day in all_day_keys if period_days and day < period_days[0]]
    daily_demand = getattr(row, "daily_demand", None) or {}
    daily_receipts = getattr(row, "daily_receipts", None) or {}

    opening = float(getattr(row, "stock", 0.0) or 0.0)
    for day in days_before:
        opening += float(daily_receipts.get(day, 0.0) or 0.0)
        opening -= float(daily_demand.get(day, 0.0) or 0.0)
        opening = max(0.0, opening)

    expected = sum(float(daily_receipts.get(day, 0.0) or 0.0) for day in period_days)
    return opening, expected


def _product_material_shortages(
    *,
    daily: DailyPlanCoverageResult | None,
    product_coverage: ProductCoverageResult | None,
    merged: list[Any],
    product: str,
    plan: float,
    status: str,
    period_days: list[str],
    all_day_keys: list[str],
    month_label: str | None,
) -> list[dict[str, Any]]:
    """Номенклатуры, которых не хватает для обеспечения плана изделия за период."""
    if status not in ("red", "yellow") or plan <= 1e-12:
        return []

    bom = daily.boms.get(product) if daily is not None else None
    if bom is None and product_coverage is not None:
        bom = product_coverage.boms.get(product)
    if bom is None or not bom.matched:
        return []

    merged_by_key = _merged_by_norm_key(merged)
    has_daily_demand = any(getattr(row, "daily_demand", None) for row in merged)
    shortages: list[dict[str, Any]] = []

    for line in bom.lines():
        if period_days and has_daily_demand:
            mat_plan = plan * line.qty_per_unit
            stock, expected = _material_period_supply(
                merged_by_key, line.norm_key, period_days, all_day_keys
            )
        elif month_label and product_coverage is not None:
            mat_plan = product_coverage.material_plan(product, month_label, line.norm_key)
            row = merged_by_key.get(line.norm_key)
            stock = float(getattr(row, "stock", 0.0) or 0.0) if row else 0.0
            expected = (
                float((getattr(row, "monthly_receipts", None) or {}).get(month_label, 0.0) or 0.0)
                if row
                else 0.0
            )
        else:
            mat_plan = plan * line.qty_per_unit
            row = merged_by_key.get(line.norm_key)
            stock = float(getattr(row, "stock", 0.0) or 0.0) if row else 0.0
            expected = 0.0

        shortage = max(0.0, mat_plan - stock - expected)
        if shortage <= 1e-9:
            continue
        shortages.append(
            {
                "name": line.nomenclature,
                "plan": round(mat_plan, 2),
                "stock": round(stock, 2),
                "expected": round(expected, 2),
                "shortage": round(shortage, 2),
            }
        )

    shortages.sort(key=lambda item: (-float(item["shortage"]), str(item["name"])))
    return shortages


def _product_status_for_period(
    daily: DailyPlanCoverageResult,
    product: str,
    period_days: list[str],
    plan: float,
) -> str:
    if plan <= 1e-12:
        return "none"
    bom = daily.boms.get(product)
    matched = bool(bom and bom.matched)
    covered = float(daily.covered_for_days(product, period_days))
    if not matched or covered <= 1e-12:
        return "red"
    if covered + 1e-9 < plan:
        return "yellow"
    return "green"


def _serialize_product_rows(
    daily: DailyPlanCoverageResult,
    period_days: list[str],
    *,
    merged: list[Any],
    all_day_keys: list[str],
    product_coverage: ProductCoverageResult | None,
    month_label: str | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for product in daily.products_in_order:
        plan = sum(float(daily.cell(product, day).plan) for day in period_days)
        fact = sum(float(daily.cell(product, day).fact) for day in period_days)
        covered = float(daily.covered_for_days(product, period_days))
        if plan <= 1e-12 and fact <= 1e-12 and covered <= 1e-12:
            continue
        status = _product_status_for_period(daily, product, period_days, plan)
        row: dict[str, Any] = {
            "name": product,
            "plan": round(plan, 2),
            "fact": round(fact, 2),
            "covered": round(covered, 2),
            "status": status,
        }
        shortages = _product_material_shortages(
            daily=daily,
            product_coverage=product_coverage,
            merged=merged,
            product=product,
            plan=plan,
            status=status,
            period_days=period_days,
            all_day_keys=all_day_keys,
            month_label=month_label,
        )
        if shortages:
            row["shortages"] = shortages
        rows.append(row)
    rows.sort(key=lambda row: (-float(row["plan"]), str(row["name"])))
    return rows


def _serialize_product_rows_monthly(
    product_coverage: ProductCoverageResult | None,
    month_label: str | None,
    *,
    merged: list[Any],
) -> list[dict[str, Any]]:
    if product_coverage is None or not month_label:
        return []
    if month_label not in product_coverage.months:
        return []
    rows: list[dict[str, Any]] = []
    for product in product_coverage.products_in_order:
        cell = product_coverage.cell(product, month_label)
        plan = float(cell.plan or 0.0)
        fact = float(cell.fact or 0.0)
        covered = float(cell.covered or 0.0)
        if plan <= 1e-12 and fact <= 1e-12 and covered <= 1e-12:
            continue
        if covered + 1e-9 >= plan:
            status = "green"
        elif covered > 1e-12:
            status = "yellow"
        else:
            status = "red"
        row: dict[str, Any] = {
            "name": product,
            "plan": round(plan, 2),
            "fact": round(fact, 2),
            "covered": round(covered, 2),
            "status": status,
        }
        shortages = _product_material_shortages(
            daily=None,
            product_coverage=product_coverage,
            merged=merged,
            product=product,
            plan=plan,
            status=status,
            period_days=[],
            all_day_keys=[],
            month_label=month_label,
        )
        if shortages:
            row["shortages"] = shortages
        rows.append(row)
    rows.sort(key=lambda row: (-float(row["plan"]), str(row["name"])))
    return rows


def _nomenclature_status(plan: float, covered: float) -> str:
    if plan <= 1e-12:
        return "none"
    if covered + 1e-9 >= plan:
        return "green"
    if covered > 1e-12:
        return "yellow"
    return "red"


def _serialize_nomenclature_rows(
    merged: Iterable[Any],
    period_days: list[str],
    all_day_keys: list[str],
) -> list[dict[str, Any]]:
    """Потребность/факт/обеспеченность номенклатур за период с дневным списанием остатка."""
    rows: list[dict[str, Any]] = []
    if not period_days:
        return rows

    days_before_period = [day for day in all_day_keys if day < period_days[0]]

    for row in merged:
        name = str(getattr(row, "nomenclature", "") or "").strip()
        if not name:
            continue
        daily_demand = getattr(row, "daily_demand", None) or {}
        daily_fact = getattr(row, "daily_demand_fact", None) or {}
        daily_receipts = getattr(row, "daily_receipts", None) or {}

        opening = float(getattr(row, "stock", 0.0) or 0.0)
        for day in days_before_period:
            opening += float(daily_receipts.get(day, 0.0) or 0.0)
            opening -= float(daily_demand.get(day, 0.0) or 0.0)
            opening = max(0.0, opening)

        plan_total = 0.0
        fact_total = 0.0
        covered_total = 0.0
        available_end = opening

        for day in period_days:
            plan_day = float(daily_demand.get(day, 0.0) or 0.0)
            fact_day = float(daily_fact.get(day, 0.0) or 0.0)
            opening += float(daily_receipts.get(day, 0.0) or 0.0)
            covered_day = min(plan_day, opening) if plan_day > 1e-12 else 0.0
            opening = max(0.0, opening - plan_day)
            plan_total += plan_day
            fact_total += fact_day
            covered_total += covered_day
            available_end = opening

        if plan_total <= 1e-12 and fact_total <= 1e-12:
            continue

        rows.append(
            {
                "name": name,
                "plan": round(plan_total, 2),
                "fact": round(fact_total, 2),
                "covered": round(covered_total, 2),
                "available": round(max(0.0, available_end), 2),
                "status": _nomenclature_status(plan_total, covered_total),
            }
        )
    rows.sort(key=lambda item: (-float(item["plan"]), str(item["name"])))
    return rows


def _serialize_nomenclature_rows_monthly(
    merged: Iterable[Any],
    month_label: str | None,
) -> list[dict[str, Any]]:
    if not month_label:
        return []
    rows: list[dict[str, Any]] = []
    for row in merged:
        name = str(getattr(row, "nomenclature", "") or "").strip()
        if not name:
            continue
        monthly_demand = getattr(row, "monthly_demand", None) or {}
        bucket = monthly_demand.get(month_label) or {}
        plan = 0.0
        fact = 0.0
        for category in ("заказ", "опытные", "склад"):
            part = bucket.get(category) or {}
            plan += float(part.get("план", 0.0) or 0.0)
            fact += float(part.get("факт", 0.0) or 0.0)
        receipts = float((getattr(row, "monthly_receipts", None) or {}).get(month_label, 0.0) or 0.0)
        stock = float(getattr(row, "stock", 0.0) or 0.0)
        available = stock + receipts
        if plan <= 1e-12 and fact <= 1e-12:
            continue
        covered = min(plan, available) if plan > 1e-12 else 0.0
        rows.append(
            {
                "name": name,
                "plan": round(plan, 2),
                "fact": round(fact, 2),
                "covered": round(covered, 2),
                "available": round(available, 2),
                "status": _nomenclature_status(plan, covered),
            }
        )
    rows.sort(key=lambda item: (-float(item["plan"]), str(item["name"])))
    return rows


def _tile_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with_plan = [row for row in rows if float(row.get("plan") or 0.0) > 1e-12]
    green_rows = [row for row in with_plan if row.get("status") == "green"]
    yellow_rows = [row for row in with_plan if row.get("status") == "yellow"]
    red_rows = [row for row in with_plan if row.get("status") == "red"]
    return {
        "all": len(with_plan),
        "green": len(green_rows),
        "yellow": len(yellow_rows),
        "red": len(red_rows),
        "plan_total": round(sum(float(row.get("plan") or 0.0) for row in with_plan), 2),
        "fact_total": round(sum(float(row.get("fact") or 0.0) for row in with_plan), 2),
        "covered_total": round(sum(float(row.get("covered") or 0.0) for row in with_plan), 2),
        "green_plan_total": round(sum(float(row.get("plan") or 0.0) for row in green_rows), 2),
        "yellow_plan_total": round(sum(float(row.get("plan") or 0.0) for row in yellow_rows), 2),
        "red_plan_total": round(sum(float(row.get("plan") or 0.0) for row in red_rows), 2),
    }


def _serialize_period(
    *,
    period: str,
    period_days: list[str],
    all_day_keys: list[str],
    daily: DailyPlanCoverageResult | None,
    detailed_plans: list[Any] | None,
    merged: list[Any],
    product_coverage: ProductCoverageResult | None,
    month_label: str | None,
) -> dict[str, Any]:
    period_daily = _daily_result_for_period(
        daily=daily,
        detailed_plans=detailed_plans,
        merged=merged,
        all_day_keys=all_day_keys,
        period_days=period_days,
    )

    product_rows: list[dict[str, Any]] = []
    if period_daily is not None and period_daily.products_in_order and period_days:
        product_rows = _serialize_product_rows(
            period_daily,
            period_days,
            merged=merged,
            all_day_keys=all_day_keys,
            product_coverage=product_coverage,
            month_label=month_label,
        )

    nomenclature_rows: list[dict[str, Any]] = []
    if period_days and any(getattr(row, "daily_demand", None) for row in merged):
        nomenclature_rows = _serialize_nomenclature_rows(merged, period_days, all_day_keys)

    return {
        "key": period,
        "label": {"day": "За день", "week": "За неделю", "month": "За месяц"}.get(period, period),
        "days": period_days,
        "products": {
            "rows": product_rows[:250],
            "tiles": _tile_stats(product_rows),
        },
        "nomenclatures": {
            "rows": nomenclature_rows[:250],
            "tiles": _tile_stats(nomenclature_rows),
        },
    }


def build_coverage_dashboard(
    *,
    daily_plan_coverage: DailyPlanCoverageResult | None,
    product_coverage: ProductCoverageResult | None,
    merged: list[Any],
    day_keys: list[str],
    detailed_plans: list[Any] | None = None,
    as_of: date | None = None,
    schedule_month: str = "",
) -> dict[str, Any] | None:
    """Payload для дашборда обеспеченности (изделия / номенклатуры × день/неделя/месяц)."""
    as_of_day = as_of or date.today()
    keys = list(day_keys or [])
    if daily_plan_coverage is not None and daily_plan_coverage.day_keys:
        keys = list(daily_plan_coverage.day_keys)
    if not keys and not merged and product_coverage is None:
        return None

    month_label = _resolve_month_label(
        schedule_month=schedule_month,
        day_keys=keys,
        product_coverage=product_coverage,
        merged=merged,
    )

    periods = {
        period: _serialize_period(
            period=period,
            period_days=_period_day_keys(keys, period, as_of_day),
            all_day_keys=keys,
            daily=daily_plan_coverage,
            detailed_plans=detailed_plans,
            merged=merged,
            product_coverage=product_coverage,
            month_label=month_label,
        )
        for period in ("day", "week", "month")
    }
    has_products = any(periods[p]["products"]["tiles"]["all"] > 0 for p in periods)
    has_nomenclatures = any(periods[p]["nomenclatures"]["tiles"]["all"] > 0 for p in periods)
    if not has_products and not has_nomenclatures:
        return None

    return {
        "as_of": as_of_day.isoformat(),
        "schedule_month": schedule_month,
        "default_period": "week",
        "periods": periods,
    }
