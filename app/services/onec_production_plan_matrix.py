"""Матрица «изделие × дата» для UI плана производства из 1С."""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol


class _PlanRowLike(Protocol):
    month_key: str
    line_number: int
    product_date: datetime | None
    nomenclature_key: str
    nomenclature_code: str
    nomenclature_name: str
    qty: float
    unit: str


_MONTH_NAMES_RU = (
    "",
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
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


def format_month_label(month_key: str) -> str:
    if not month_key or len(month_key) < 7 or "-" not in month_key:
        return month_key or "—"
    try:
        year_s, month_s = month_key.split("-", 1)
        month_num = int(month_s)
        if 1 <= month_num <= 12:
            return f"{_MONTH_NAMES_RU[month_num]} {year_s}"
    except ValueError:
        pass
    return month_key


def _parse_month_key(month_key: str) -> tuple[int, int] | None:
    if not month_key or "-" not in month_key:
        return None
    try:
        year, month = month_key.split("-", 1)
        y, m = int(year), int(month)
        if y < 1900 or m < 1 or m > 12:
            return None
        return y, m
    except ValueError:
        return None


def _month_day_keys(month_key: str) -> list[str]:
    parsed = _parse_month_key(month_key)
    if parsed is None:
        return []
    year, month = parsed
    last_day = calendar.monthrange(year, month)[1]
    return [f"{month_key}-{day:02d}" for day in range(1, last_day + 1)]


def _day_label(date_key: str, *, granularity: str) -> str:
    if granularity == "month":
        return format_month_label(date_key)
    if len(date_key) >= 10 and date_key[4] == "-":
        try:
            day = int(date_key[8:10])
            return f"{day:02d}"
        except ValueError:
            pass
    return date_key


def _clean_ref(value: str | None) -> str:
    text = (value or "").strip()
    return "" if not text or text == EMPTY_GUID else text


def _product_group_key(row: _PlanRowLike) -> str:
    # В 1С пустой GUID может приходить как обычное значение. Для UI и агрегации
    # надёжнее группировать по реальному имени, как это делает расчёт daily-плана.
    name = (row.nomenclature_name or "").strip()
    code = (row.nomenclature_code or "").strip()
    ref_key = _clean_ref(row.nomenclature_key)
    if name:
        return f"name:{name.casefold().replace('ё', 'е')}"
    if code:
        return f"code:{code.casefold()}"
    if ref_key:
        return f"ref:{ref_key}"
    return f"line:{row.line_number}"


@dataclass(frozen=True)
class ProductionPlanProductMatrix:
    product_key: str
    name: str
    code: str
    unit: str
    qty_by_date: dict[str, float]
    month_only_qty: float
    total: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_key": self.product_key,
            "name": self.name,
            "code": self.code,
            "unit": self.unit,
            "qty_by_date": self.qty_by_date,
            "month_only_qty": self.month_only_qty,
            "total": self.total,
        }


@dataclass(frozen=True)
class ProductionPlanMonthMatrix:
    month_key: str
    month_label: str
    granularity: str  # "day" | "month"
    date_keys: list[str]
    date_labels: list[str]
    has_undated: bool
    products: list[ProductionPlanProductMatrix]
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "month_key": self.month_key,
            "month_label": self.month_label,
            "granularity": self.granularity,
            "date_keys": self.date_keys,
            "date_labels": self.date_labels,
            "has_undated": self.has_undated,
            "products": [p.to_dict() for p in self.products],
            "note": self.note,
        }


def build_month_matrix(month_key: str, rows: list[_PlanRowLike]) -> ProductionPlanMonthMatrix:
    """Строит матрицу для одного месяца: изделия слева, даты сверху."""
    products_map: dict[str, dict[str, Any]] = {}
    has_any_day = False

    for row in rows:
        product_key = _product_group_key(row)
        if product_key not in products_map:
            products_map[product_key] = {
                "product_key": product_key,
                "name": (row.nomenclature_name or product_key).strip(),
                "code": (row.nomenclature_code or "").strip(),
                "unit": _clean_ref(row.unit),
                "qty_by_date": {},
                "month_only_qty": 0.0,
            }
        bucket = products_map[product_key]
        qty = float(row.qty or 0.0)
        if row.product_date is not None:
            day_key = row.product_date.date().isoformat()
            if day_key.startswith(month_key):
                bucket["qty_by_date"][day_key] = bucket["qty_by_date"].get(day_key, 0.0) + qty
                has_any_day = True
            else:
                bucket["month_only_qty"] += qty
        else:
            bucket["month_only_qty"] += qty

    product_rows: list[ProductionPlanProductMatrix] = []
    for entry in sorted(products_map.values(), key=lambda item: str(item["name"]).casefold()):
        qty_by_date: dict[str, float] = entry["qty_by_date"]
        month_only = float(entry["month_only_qty"])
        total = sum(qty_by_date.values()) + month_only
        product_rows.append(
            ProductionPlanProductMatrix(
                product_key=str(entry["product_key"]),
                name=str(entry["name"]),
                code=str(entry["code"]),
                unit=str(entry["unit"]),
                qty_by_date=qty_by_date,
                month_only_qty=month_only,
                total=total,
            )
        )

    granularity = "day" if has_any_day else "month"
    if granularity == "day":
        date_keys = _month_day_keys(month_key)
        note = ""
        if any(p.month_only_qty for p in product_rows):
            note = (
                "Часть строк из 1С без даты выпуска — колонка «Без даты» "
                "(план на месяц без привязки к дню)."
            )
    else:
        date_keys = [month_key] if month_key else []
        note = (
            "В 1С у строк нет даты выпуска (ДатаПотребности / ДатаВыпуска / Период / Дата) — "
            "показан суммарный план на месяц."
        )

    date_labels = [_day_label(key, granularity=granularity) for key in date_keys]
    has_undated = any(p.month_only_qty > 0 for p in product_rows)

    return ProductionPlanMonthMatrix(
        month_key=month_key,
        month_label=format_month_label(month_key),
        granularity=granularity,
        date_keys=date_keys,
        date_labels=date_labels,
        has_undated=has_undated,
        products=product_rows,
        note=note,
    )


def build_production_plan_matrices(rows: list[_PlanRowLike]) -> dict[str, Any]:
    """Все месяцы документа + список ключей для переключателя."""
    by_month: dict[str, list[_PlanRowLike]] = defaultdict(list)
    for row in rows:
        month_key = (row.month_key or "").strip() or "unknown"
        by_month[month_key].append(row)

    month_keys = sorted(
        (key for key in by_month if key != "unknown"),
        reverse=True,
    )
    if "unknown" in by_month:
        month_keys.append("unknown")

    matrices = {
        month_key: build_month_matrix(month_key, month_rows).to_dict()
        for month_key, month_rows in by_month.items()
    }
    default_month = month_keys[0] if month_keys else ""

    return {
        "month_keys": month_keys,
        "default_month": default_month,
        "matrices": matrices,
    }
