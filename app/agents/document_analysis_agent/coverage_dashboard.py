"""Сериализация дашборда обеспеченности для UI руководителя."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Iterable

from app.agents.document_analysis_agent.material_classification import (
    MATERIAL_KIND_REQUIRED,
    is_optional_material_kind,
)
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


def _schedule_month_from_parts(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _schedule_month_from_date(as_of: date) -> str:
    return _schedule_month_from_parts(as_of.year, as_of.month)


def resolve_coverage_target_month(
    *,
    schedule_month: str = "",
    as_of: date | None = None,
    product_coverage: ProductCoverageResult | None = None,
    merged: Iterable[Any] | None = None,
) -> tuple[str, str]:
    """Целевой месяц дашборда: (YYYY-MM, «Август»)."""
    label = _month_label_from_iso(schedule_month)
    if label and schedule_month:
        return schedule_month, label
    as_of_day = as_of or date.today()
    resolved_month = _schedule_month_from_date(as_of_day)
    resolved_label = _MONTH_ORDER[as_of_day.month - 1]
    if product_coverage and product_coverage.months:
        for month in product_coverage.months:
            if month == resolved_label or month == resolved_month:
                return resolved_month, resolved_label
            iso_label = _month_label_from_iso(month)
            if iso_label == resolved_label or month == resolved_month:
                if "-" in month and len(month) == 7:
                    return month, resolved_label
                return resolved_month, iso_label or resolved_label
    if merged is not None:
        for row in merged:
            monthly_demand = getattr(row, "monthly_demand", None) or {}
            if resolved_label in monthly_demand:
                return resolved_month, resolved_label
            if resolved_month in monthly_demand:
                return resolved_month, resolved_label
    return resolved_month, resolved_label


def _filter_day_keys_to_schedule_month(day_keys: list[str], schedule_month: str) -> list[str]:
    if not schedule_month or len(schedule_month) != 7 or schedule_month[4] != "-":
        return list(day_keys)
    return [day for day in day_keys if day.startswith(schedule_month)]


def _monthly_bucket(
    monthly_map: dict[str, Any] | None,
    *,
    schedule_month: str,
    month_label: str,
) -> dict[str, Any]:
    if not monthly_map:
        return {}
    if month_label in monthly_map:
        return monthly_map.get(month_label) or {}
    if schedule_month in monthly_map:
        return monthly_map.get(schedule_month) or {}
    for key, bucket in monthly_map.items():
        if key == month_label or key == schedule_month:
            return bucket or {}
        iso_label = _month_label_from_iso(key)
        if iso_label == month_label:
            return bucket or {}
    return {}


def _has_nonzero_daily_product_plan(
    daily: DailyPlanCoverageResult | None,
    period_days: list[str],
) -> bool:
    if daily is None or not period_days:
        return False
    for product in daily.products_in_order:
        for day in period_days:
            if float(daily.cell(product, day).plan or 0.0) > 1e-12:
                return True
    return False


def _has_nonzero_daily_nomenclature_demand(
    merged: Iterable[Any],
    period_days: list[str],
) -> bool:
    if not period_days:
        return False
    for row in merged:
        daily_demand = getattr(row, "daily_demand", None) or {}
        if any(float(daily_demand.get(day, 0.0) or 0.0) > 1e-12 for day in period_days):
            return True
    return False


def resolve_plan_month_keys(
    schedule_plans: Iterable[Any],
    *,
    schedule_month: str,
    month_label: str,
) -> list[str]:
    """Ключ месяца в schedule_plans.monthly_qty для целевого месяца."""
    keys: set[str] = set()
    for plan in schedule_plans:
        monthly = getattr(plan, "monthly_qty", None) or {}
        keys.update(str(key) for key in monthly.keys())
    if month_label in keys:
        return [month_label]
    if schedule_month in keys:
        return [schedule_month]
    for key in keys:
        if _month_label_from_iso(key) == month_label:
            return [key]
    if month_label:
        return [month_label]
    return []


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
    as_of: date | None = None,
) -> str | None:
    _, label = resolve_coverage_target_month(
        schedule_month=schedule_month,
        as_of=as_of,
        product_coverage=product_coverage,
        merged=merged,
    )
    if label:
        return label
    label = _month_label_from_day_keys(day_keys)
    if label:
        return label
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

    if daily is not None and set(sim_days).issubset(set(daily.day_keys)):
        return daily

    if detailed_plans:
        return compute_daily_plan_coverage(detailed_plans, merged, sim_days)

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


def _matching_product_coverage_month(
    product_coverage: ProductCoverageResult,
    *,
    schedule_month: str,
    month_label: str,
) -> str | None:
    if month_label in product_coverage.months:
        return month_label
    if schedule_month in product_coverage.months:
        return schedule_month
    for month in product_coverage.months:
        if _month_label_from_iso(month) == month_label:
            return month
    return None


def _product_bom_material_lines(
    *,
    daily: DailyPlanCoverageResult | None,
    product_coverage: ProductCoverageResult | None,
    merged: list[Any],
    product: str,
    plan: float,
    period_days: list[str],
    all_day_keys: list[str],
    month_label: str | None,
    schedule_month: str = "",
) -> list[dict[str, Any]]:
    """Все номенклатуры спецификации изделия с планом, остатком и дефицитом за период."""
    if plan <= 1e-12:
        return []

    bom = daily.boms.get(product) if daily is not None else None
    if bom is None and product_coverage is not None:
        bom = product_coverage.boms.get(product)
    if bom is None or not bom.matched:
        return []

    merged_by_key = _merged_by_norm_key(merged)
    has_daily_demand = any(getattr(row, "daily_demand", None) for row in merged)
    lines: list[dict[str, Any]] = []

    for line in bom.lines():
        if period_days and has_daily_demand:
            mat_plan = plan * line.qty_per_unit
            stock, expected = _material_period_supply(
                merged_by_key, line.norm_key, period_days, all_day_keys
            )
        elif month_label and product_coverage is not None:
            coverage_month = month_label
            if product_coverage.months:
                matched = _matching_product_coverage_month(
                    product_coverage,
                    schedule_month=schedule_month,
                    month_label=month_label,
                )
                if matched:
                    coverage_month = matched
            mat_plan = product_coverage.material_plan(product, coverage_month, line.norm_key)
            row = merged_by_key.get(line.norm_key)
            stock = float(getattr(row, "stock", 0.0) or 0.0) if row else 0.0
            expected = (
                _monthly_scalar(
                    getattr(row, "monthly_receipts", None) or {} if row else {},
                    schedule_month=schedule_month,
                    month_label=month_label,
                )
                if row
                else 0.0
            )
        else:
            mat_plan = plan * line.qty_per_unit
            row = merged_by_key.get(line.norm_key)
            stock = float(getattr(row, "stock", 0.0) or 0.0) if row else 0.0
            expected = 0.0

        shortage = max(0.0, mat_plan - stock - expected)
        material_kind = getattr(line, "material_kind", MATERIAL_KIND_REQUIRED)
        lines.append(
            {
                "name": line.nomenclature,
                "plan": round(mat_plan, 2),
                "stock": round(stock, 2),
                "expected": round(expected, 2),
                "shortage": round(shortage, 2),
                "materialKind": material_kind,
                "materialKindLabel": getattr(line, "material_kind_label", "") or "",
                "materialKindConfidence": getattr(line, "material_kind_confidence", "") or "",
                "materialKindReason": getattr(line, "material_kind_reason", "") or "",
                "optional": is_optional_material_kind(material_kind),
            }
        )

    lines.sort(
        key=lambda item: (
            -float(item["shortage"]),
            -float(item["stock"]) - float(item["expected"]),
            str(item["name"]),
        )
    )
    return lines


def _product_material_shortages(
    *,
    daily: DailyPlanCoverageResult | None,
    product_coverage: ProductCoverageResult | None,
    merged: list[Any],
    product: str,
    plan: float,
    period_days: list[str],
    all_day_keys: list[str],
    month_label: str | None,
    schedule_month: str = "",
) -> list[dict[str, Any]]:
    """Только номенклатуры с дефицитом (для сводки «проблемные номенклатуры»)."""
    return [
        line
        for line in _product_bom_material_lines(
            daily=daily,
            product_coverage=product_coverage,
            merged=merged,
            product=product,
            plan=plan,
            period_days=period_days,
            all_day_keys=all_day_keys,
            month_label=month_label,
            schedule_month=schedule_month,
        )
        if float(line.get("shortage") or 0.0) > 1e-9
    ]


def _product_has_any_material_supply(materials: list[dict[str, Any]]) -> bool:
    return any(
        float(item.get("stock") or 0.0) + float(item.get("expected") or 0.0) > 1e-12
        for item in materials
    )


def _product_assemblable_from_material_lines(
    materials: list[dict[str, Any]],
    plan: float,
    *,
    ignore_optional: bool = False,
) -> float:
    """Сколько полных П/ф можно собрать из доступных номенклатур (узкое место по спеке).

    Считаем по каждому изделию отдельно: floor(остаток+поступление / норма на единицу),
    без перераспределения материалов между изделиями (в отличие от fair-share covered).
    """
    if plan <= 1e-12:
        return 0.0
    lines = [
        line
        for line in materials
        if not (ignore_optional and line.get("optional"))
    ]
    if not lines:
        return 0.0
    buildable = float(plan)
    for line in lines:
        mat_plan = float(line.get("plan") or 0.0)
        if mat_plan <= 1e-12:
            continue
        supply = float(line.get("stock") or 0.0) + float(line.get("expected") or 0.0)
        units_from_line = math.floor(supply * plan / mat_plan + 1e-9)
        buildable = min(buildable, float(units_from_line))
    return max(0.0, min(buildable, plan))


def _product_assemblable_fields(
    *,
    plan: float,
    strict_covered: float,
    materials: list[dict[str, Any]],
) -> dict[str, float]:
    if materials:
        assemblable = _product_assemblable_from_material_lines(materials, plan)
    else:
        assemblable = min(plan, max(0.0, strict_covered))
    return {
        "assemblableQty": round(assemblable, 2),
    }


def _product_status_for_period(
    daily: DailyPlanCoverageResult,
    product: str,
    period_days: list[str],
    plan: float,
    *,
    materials: list[dict[str, Any]] | None = None,
    assemblable_qty: float = 0.0,
) -> str:
    if plan <= 1e-12:
        return "none"
    bom = daily.boms.get(product)
    matched = bool(bom and bom.matched)
    if assemblable_qty + 1e-9 >= plan:
        return "green"
    if materials:
        return "yellow" if _product_has_any_material_supply(materials) else "red"
    if not matched:
        return "red"
    covered = float(daily.covered_for_days(product, period_days))
    if covered > 1e-12:
        return "yellow"
    return "red"


def _product_status_for_monthly(
    *,
    plan: float,
    strict_covered: float,
    materials: list[dict[str, Any]],
    assemblable_qty: float = 0.0,
) -> str:
    if plan <= 1e-12:
        return "none"
    if assemblable_qty + 1e-9 >= plan:
        return "green"
    if materials:
        return "yellow" if _product_has_any_material_supply(materials) else "red"
    if strict_covered > 1e-12:
        return "yellow"
    return "red"


def _product_in_spec_scope(product: str, spec_eligible_products: frozenset[str] | None) -> bool:
    if not spec_eligible_products:
        return True
    return _normalize(product) in {_normalize(name) for name in spec_eligible_products}


def _serialize_product_rows(
    daily: DailyPlanCoverageResult,
    period_days: list[str],
    *,
    merged: list[Any],
    all_day_keys: list[str],
    product_coverage: ProductCoverageResult | None,
    month_label: str | None,
    schedule_month: str = "",
    spec_eligible_products: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for product in daily.products_in_order:
        if not _product_in_spec_scope(product, spec_eligible_products):
            continue
        plan = sum(float(daily.cell(product, day).plan) for day in period_days)
        fact = sum(float(daily.cell(product, day).fact) for day in period_days)
        strict_covered = float(daily.covered_for_days(product, period_days))
        materials = _product_bom_material_lines(
            daily=daily,
            product_coverage=product_coverage,
            merged=merged,
            product=product,
            plan=plan,
            period_days=period_days,
            all_day_keys=all_day_keys,
            month_label=month_label,
            schedule_month=schedule_month,
        ) if plan > 1e-12 else []
        assemblable_fields = _product_assemblable_fields(
            plan=plan,
            strict_covered=strict_covered,
            materials=materials,
        )
        assemblable_qty = float(assemblable_fields["assemblableQty"])
        covered = assemblable_qty if materials else strict_covered
        if plan <= 1e-12 and fact <= 1e-12 and covered <= 1e-12:
            continue
        status = _product_status_for_period(
            daily,
            product,
            period_days,
            plan,
            materials=materials,
            assemblable_qty=assemblable_qty,
        )
        row: dict[str, Any] = {
            "name": product,
            "plan": round(plan, 2),
            "fact": round(fact, 2),
            "covered": round(covered, 2),
            "status": status,
            **assemblable_fields,
        }
        if materials:
            row["materials"] = materials
            shortages = [
                item for item in materials if float(item.get("shortage") or 0.0) > 1e-9
            ]
            if shortages:
                row["shortages"] = shortages
        rows.append(row)
    rows.sort(key=lambda row: (-float(row["plan"]), str(row["name"])))
    return rows


def _serialize_product_rows_monthly(
    product_coverage: ProductCoverageResult | None,
    month_label: str | None,
    *,
    schedule_month: str = "",
    merged: list[Any],
    spec_eligible_products: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    if product_coverage is None or not month_label:
        return []
    coverage_month = _matching_product_coverage_month(
        product_coverage,
        schedule_month=schedule_month,
        month_label=month_label,
    )
    if coverage_month is None:
        return []
    rows: list[dict[str, Any]] = []
    for product in product_coverage.products_in_order:
        if not _product_in_spec_scope(product, spec_eligible_products):
            continue
        cell = product_coverage.cell(product, coverage_month)
        plan = float(cell.plan or 0.0)
        fact = float(cell.fact or 0.0)
        strict_covered = float(cell.covered or 0.0)
        materials = _product_bom_material_lines(
            daily=None,
            product_coverage=product_coverage,
            merged=merged,
            product=product,
            plan=plan,
            period_days=[],
            all_day_keys=[],
            month_label=month_label,
            schedule_month=schedule_month,
        ) if plan > 1e-12 else []
        assemblable_fields = _product_assemblable_fields(
            plan=plan,
            strict_covered=strict_covered,
            materials=materials,
        )
        assemblable_qty = float(assemblable_fields["assemblableQty"])
        covered = assemblable_qty if materials else strict_covered
        if plan <= 1e-12 and fact <= 1e-12 and covered <= 1e-12:
            continue
        status = _product_status_for_monthly(
            plan=plan,
            strict_covered=strict_covered,
            materials=materials,
            assemblable_qty=assemblable_qty,
        )
        row: dict[str, Any] = {
            "name": product,
            "plan": round(plan, 2),
            "fact": round(fact, 2),
            "covered": round(covered, 2),
            "status": status,
            **assemblable_fields,
        }
        if materials:
            row["materials"] = materials
            shortages = [
                item for item in materials if float(item.get("shortage") or 0.0) > 1e-9
            ]
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


def _material_meta_from_row(row: Any) -> dict[str, Any]:
    kind = str(getattr(row, "coverage_material_kind", "") or MATERIAL_KIND_REQUIRED)
    return {
        "materialKind": kind,
        "materialKindLabel": str(getattr(row, "coverage_material_label", "") or ""),
        "materialKindConfidence": str(
            getattr(row, "coverage_material_confidence", "") or ""
        ),
        "materialKindReason": str(getattr(row, "coverage_material_reason", "") or ""),
        "optional": is_optional_material_kind(kind),
    }


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
                **_material_meta_from_row(row),
            }
        )
    rows.sort(key=lambda item: (-float(item["plan"]), str(item["name"])))
    return rows


def _monthly_scalar(
    monthly_map: dict[str, Any] | None,
    *,
    schedule_month: str,
    month_label: str,
) -> float:
    if not monthly_map:
        return 0.0
    if month_label in monthly_map:
        return float(monthly_map.get(month_label, 0.0) or 0.0)
    if schedule_month in monthly_map:
        return float(monthly_map.get(schedule_month, 0.0) or 0.0)
    for key, value in monthly_map.items():
        if _month_label_from_iso(str(key)) == month_label:
            return float(value or 0.0)
    return 0.0


def _serialize_nomenclature_rows_monthly(
    merged: Iterable[Any],
    month_label: str | None,
    *,
    schedule_month: str = "",
) -> list[dict[str, Any]]:
    if not month_label:
        return []
    rows: list[dict[str, Any]] = []
    for row in merged:
        name = str(getattr(row, "nomenclature", "") or "").strip()
        if not name:
            continue
        monthly_demand = getattr(row, "monthly_demand", None) or {}
        bucket = _monthly_bucket(
            monthly_demand,
            schedule_month=schedule_month,
            month_label=month_label,
        )
        plan = 0.0
        fact = 0.0
        for category in ("заказ", "опытные", "склад"):
            part = bucket.get(category) or {}
            plan += float(part.get("план", 0.0) or 0.0)
            fact += float(part.get("факт", 0.0) or 0.0)
        receipts = _monthly_scalar(
            getattr(row, "monthly_receipts", None) or {},
            schedule_month=schedule_month,
            month_label=month_label,
        )
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
                **_material_meta_from_row(row),
            }
        )
    rows.sort(key=lambda item: (-float(item["plan"]), str(item["name"])))
    return rows


def _tile_stats(rows: list[dict[str, Any]], *, ignore_optional: bool = False) -> dict[str, Any]:
    all_with_plan = [row for row in rows if float(row.get("plan") or 0.0) > 1e-12]
    optional_rows = [row for row in all_with_plan if row.get("optional")]
    with_plan = (
        [row for row in all_with_plan if not row.get("optional")]
        if ignore_optional
        else list(all_with_plan)
    )
    green_rows = [row for row in with_plan if row.get("status") == "green"]
    yellow_rows = [row for row in with_plan if row.get("status") == "yellow"]
    red_rows = [row for row in with_plan if row.get("status") == "red"]
    green_plan_total = sum(float(row.get("plan") or 0.0) for row in green_rows)
    yellow_plan_total = sum(float(row.get("plan") or 0.0) for row in yellow_rows)
    red_plan_total = sum(float(row.get("plan") or 0.0) for row in red_rows)
    green_covered_total = sum(float(row.get("covered") or 0.0) for row in green_rows)
    yellow_covered_total = sum(float(row.get("covered") or 0.0) for row in yellow_rows)
    red_covered_total = sum(float(row.get("covered") or 0.0) for row in red_rows)
    return {
        "all": len(with_plan),
        "green": len(green_rows),
        "yellow": len(yellow_rows),
        "red": len(red_rows),
        "plan_total": round(sum(float(row.get("plan") or 0.0) for row in with_plan), 2),
        "fact_total": round(sum(float(row.get("fact") or 0.0) for row in with_plan), 2),
        "covered_total": round(sum(float(row.get("covered") or 0.0) for row in with_plan), 2),
        "green_plan_total": round(green_plan_total, 2),
        "yellow_plan_total": round(yellow_plan_total, 2),
        "red_plan_total": round(red_plan_total, 2),
        "green_covered_total": round(green_covered_total, 2),
        "yellow_covered_total": round(yellow_covered_total, 2),
        "red_covered_total": round(red_covered_total, 2),
        "optional": len(optional_rows),
        "optional_plan_total": round(
            sum(float(row.get("plan") or 0.0) for row in optional_rows), 2
        ),
        "optional_covered_total": round(
            sum(float(row.get("covered") or 0.0) for row in optional_rows), 2
        ),
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
    schedule_month: str = "",
    spec_eligible_products: frozenset[str] | None = None,
) -> dict[str, Any]:
    month_day_keys = _filter_day_keys_to_schedule_month(all_day_keys, schedule_month)
    scoped_day_keys = month_day_keys or list(all_day_keys)
    scoped_period_days = [day for day in period_days if day in set(scoped_day_keys)] or list(period_days)

    period_daily = _daily_result_for_period(
        daily=daily,
        detailed_plans=detailed_plans,
        merged=merged,
        all_day_keys=scoped_day_keys,
        period_days=scoped_period_days,
    )

    month_has_daily_products = _has_nonzero_daily_product_plan(period_daily, scoped_day_keys)
    month_has_daily_nomenclatures = _has_nonzero_daily_nomenclature_demand(merged, scoped_day_keys)

    product_rows: list[dict[str, Any]] = []
    if month_label and not month_has_daily_products:
        product_rows = _serialize_product_rows_monthly(
            product_coverage,
            month_label,
            schedule_month=schedule_month,
            merged=merged,
            spec_eligible_products=spec_eligible_products,
        )
    elif period_daily is not None and period_daily.products_in_order and scoped_period_days:
        product_rows = _serialize_product_rows(
            period_daily,
            scoped_period_days,
            merged=merged,
            all_day_keys=scoped_day_keys,
            product_coverage=product_coverage,
            month_label=month_label,
            schedule_month=schedule_month,
            spec_eligible_products=spec_eligible_products,
        )

    nomenclature_rows: list[dict[str, Any]] = []
    if month_label and not month_has_daily_nomenclatures:
        nomenclature_rows = _serialize_nomenclature_rows_monthly(
            merged,
            month_label,
            schedule_month=schedule_month,
        )
    elif scoped_period_days and month_has_daily_nomenclatures:
        nomenclature_rows = _serialize_nomenclature_rows(merged, scoped_period_days, scoped_day_keys)

    return {
        "key": period,
        "label": {
            "day": "За день",
            "week": "За неделю",
            "month": "За месяц",
            "custom": "Период",
        }.get(period, period),
        "days": scoped_period_days,
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
    spec_eligible_products: frozenset[str] | None = None,
) -> dict[str, Any] | None:
    """Payload для дашборда обеспеченности (изделия / номенклатуры × день/неделя/месяц)."""
    as_of_day = as_of or date.today()
    keys = list(day_keys or [])
    if daily_plan_coverage is not None and daily_plan_coverage.day_keys:
        keys = list(daily_plan_coverage.day_keys)
    if not keys and not merged and product_coverage is None:
        return None

    resolved_schedule_month, month_label = resolve_coverage_target_month(
        schedule_month=schedule_month,
        as_of=as_of_day,
        product_coverage=product_coverage,
        merged=merged,
    )
    keys = _filter_day_keys_to_schedule_month(keys, resolved_schedule_month)

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
            schedule_month=resolved_schedule_month,
            spec_eligible_products=spec_eligible_products,
        )
        for period in ("day", "week", "month")
    }
    has_products = any(periods[p]["products"]["tiles"]["all"] > 0 for p in periods)
    has_nomenclatures = any(periods[p]["nomenclatures"]["tiles"]["all"] > 0 for p in periods)
    if not has_products and not has_nomenclatures:
        return None

    return {
        "as_of": as_of_day.isoformat(),
        "schedule_month": resolved_schedule_month,
        "default_period": "week",
        "periods": periods,
    }


def period_day_keys_for_range(
    all_day_keys: list[str],
    date_from: date,
    date_to: date,
) -> list[str]:
    """Дни детального графика в произвольном диапазоне дат."""
    start_iso = date_from.isoformat()
    end_iso = date_to.isoformat()
    if start_iso > end_iso:
        return []
    return [day for day in all_day_keys if start_iso <= day <= end_iso]


def build_coverage_period_for_range(
    *,
    daily_plan_coverage: DailyPlanCoverageResult | None,
    product_coverage: ProductCoverageResult | None,
    merged: list[Any],
    day_keys: list[str],
    detailed_plans: list[Any] | None = None,
    as_of: date | None = None,
    schedule_month: str = "",
    spec_eligible_products: frozenset[str] | None = None,
    date_from: date,
    date_to: date,
) -> dict[str, Any] | None:
    """Один период обеспеченности для произвольного диапазона дат."""
    as_of_day = as_of or date.today()
    keys = list(day_keys or [])
    if daily_plan_coverage is not None and daily_plan_coverage.day_keys:
        keys = list(daily_plan_coverage.day_keys)
    if not keys and not merged and product_coverage is None:
        return None

    resolved_schedule_month, month_label = resolve_coverage_target_month(
        schedule_month=schedule_month,
        as_of=as_of_day,
        product_coverage=product_coverage,
        merged=merged,
    )
    keys = _filter_day_keys_to_schedule_month(keys, resolved_schedule_month)
    period_days = period_day_keys_for_range(keys, date_from, date_to)
    if not period_days:
        return None

    return _serialize_period(
        period="custom",
        period_days=period_days,
        all_day_keys=keys,
        daily=daily_plan_coverage,
        detailed_plans=detailed_plans,
        merged=merged,
        product_coverage=product_coverage,
        month_label=month_label,
        schedule_month=resolved_schedule_month,
        spec_eligible_products=spec_eligible_products,
    )


def dump_coverage_rebuild(
    *,
    merged: list[Any],
    detailed_plans: list[Any] | None,
    day_keys: list[str],
    as_of: date,
    schedule_month: str,
    spec_eligible_products: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Компактный снимок данных для быстрого пересчёта периода без повторного анализа."""
    return {
        "version": 1,
        "as_of": as_of.isoformat(),
        "schedule_month": schedule_month,
        "day_keys": list(day_keys),
        "spec_eligible_products": sorted(spec_eligible_products or ()),
        "plans": [
            {
                "product": str(getattr(plan, "product", "") or ""),
                "daily_qty": dict(getattr(plan, "daily_qty", None) or {}),
                "daily_fact": dict(getattr(plan, "daily_fact", None) or {}),
            }
            for plan in (detailed_plans or [])
            if str(getattr(plan, "product", "") or "").strip()
        ],
        "merged": [
            {
                "nomenclature": str(getattr(row, "nomenclature", "") or ""),
                "by_product": {
                    str(key): float(value or 0.0)
                    for key, value in dict(getattr(row, "by_product", None) or {}).items()
                },
                "stock": float(getattr(row, "stock", 0.0) or 0.0),
                "daily_demand": dict(getattr(row, "daily_demand", None) or {}),
                "daily_demand_fact": dict(getattr(row, "daily_demand_fact", None) or {}),
                "daily_receipts": dict(getattr(row, "daily_receipts", None) or {}),
                "monthly_demand": dict(getattr(row, "monthly_demand", None) or {}),
                "monthly_receipts": dict(getattr(row, "monthly_receipts", None) or {}),
                "coverage_material_kind": str(getattr(row, "coverage_material_kind", "") or ""),
                "coverage_material_label": str(getattr(row, "coverage_material_label", "") or ""),
                "coverage_material_confidence": str(
                    getattr(row, "coverage_material_confidence", "") or ""
                ),
                "coverage_material_reason": str(getattr(row, "coverage_material_reason", "") or ""),
            }
            for row in merged
            if str(getattr(row, "nomenclature", "") or "").strip()
        ],
    }


def coverage_period_from_snapshot(
    snapshot: dict[str, Any] | None,
    date_from: date,
    date_to: date,
) -> dict[str, Any] | None:
    """Пересчёт периода: кэш анализа, иначе готовый day/week/month из снимка."""
    if not isinstance(snapshot, dict):
        return None
    cache = snapshot.get("coverage_rebuild")
    if isinstance(cache, dict):
        rebuilt = rebuild_coverage_period_from_cache(cache, date_from, date_to)
        if rebuilt is not None:
            return rebuilt
    coverage = snapshot.get("coverage_dashboard")
    if not isinstance(coverage, dict):
        return None
    periods = coverage.get("periods")
    if not isinstance(periods, dict):
        return None
    start_iso = date_from.isoformat()
    end_iso = date_to.isoformat()
    for key in ("day", "week", "month"):
        period = periods.get(key)
        if not isinstance(period, dict):
            continue
        days = [str(day) for day in (period.get("days") or []) if day]
        if days and days[0] == start_iso and days[-1] == end_iso:
            return {**period, "key": "custom", "label": "Период"}
    return None


def rebuild_coverage_period_from_cache(
    cache: dict[str, Any] | None,
    date_from: date,
    date_to: date,
) -> dict[str, Any] | None:
    """Пересчёт одного периода из снимка анализа — без Excel, 1С и result.xlsx."""
    if not isinstance(cache, dict):
        return None

    from types import SimpleNamespace

    as_of_raw = str(cache.get("as_of") or "")
    try:
        as_of_day = date.fromisoformat(as_of_raw) if as_of_raw else date.today()
    except ValueError:
        as_of_day = date.today()

    day_keys = [str(day) for day in (cache.get("day_keys") or []) if day]
    plans = [
        SimpleNamespace(
            product=str(item.get("product") or ""),
            daily_qty=dict(item.get("daily_qty") or {}),
            daily_fact=dict(item.get("daily_fact") or {}),
        )
        for item in (cache.get("plans") or [])
        if isinstance(item, dict) and item.get("product")
    ]
    merged = [
        SimpleNamespace(
            nomenclature=str(item.get("nomenclature") or ""),
            by_product=dict(item.get("by_product") or {}),
            stock=float(item.get("stock") or 0.0),
            daily_demand=dict(item.get("daily_demand") or {}),
            daily_demand_fact=dict(item.get("daily_demand_fact") or {}),
            daily_receipts=dict(item.get("daily_receipts") or {}),
            monthly_demand=dict(item.get("monthly_demand") or {}),
            monthly_receipts=dict(item.get("monthly_receipts") or {}),
            coverage_material_kind=str(item.get("coverage_material_kind") or ""),
            coverage_material_label=str(item.get("coverage_material_label") or ""),
            coverage_material_confidence=str(item.get("coverage_material_confidence") or ""),
            coverage_material_reason=str(item.get("coverage_material_reason") or ""),
        )
        for item in (cache.get("merged") or [])
        if isinstance(item, dict) and item.get("nomenclature")
    ]
    eligible_raw = cache.get("spec_eligible_products") or []
    spec_eligible = frozenset(str(name) for name in eligible_raw if name) or None
    daily = compute_daily_plan_coverage(plans, merged, day_keys) if plans and day_keys else None
    return build_coverage_period_for_range(
        daily_plan_coverage=daily,
        product_coverage=None,
        merged=merged,
        day_keys=day_keys,
        detailed_plans=plans,
        as_of=as_of_day,
        schedule_month=str(cache.get("schedule_month") or ""),
        spec_eligible_products=spec_eligible,
        date_from=date_from,
        date_to=date_to,
    )
