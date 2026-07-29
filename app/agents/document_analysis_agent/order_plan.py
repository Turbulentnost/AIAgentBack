"""План заказов: дата заказа и количество по номенклатурам на месяц."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_LEAD_DAYS = 21
_CUSTOMS_DAYS = 2
_SCHEDULE_CATEGORIES = ("заказ", "опытные", "склад")
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


@dataclass
class OrderPlanCell:
    month: str
    order_date: date
    qty: float


@dataclass
class OrderPlanResult:
    months: list[str]
    nomenclatures: list[str]  # display names, order as merged
    year: int
    cells: dict[tuple[str, str], OrderPlanCell] = field(default_factory=dict)
    # norm_key → lead days used
    leads: dict[str, int] = field(default_factory=dict)

    def cell(self, nomenclature: str, month: str) -> OrderPlanCell | None:
        return self.cells.get((nomenclature, month))


def month_to_number(month: str) -> int | None:
    try:
        return _MONTH_ORDER.index(month) + 1
    except ValueError:
        return None


def first_day_of_month(year: int, month_name: str) -> date | None:
    month_n = month_to_number(month_name)
    if month_n is None:
        return None
    return date(year, month_n, 1)


def lead_days_for(
    norm_key: str,
    logistics_index: Mapping[str, tuple[int, int]] | None,
) -> int:
    """Суммарный lead: long_MSK + таможня + long_Ростов; иначе 21."""
    if not logistics_index:
        return _DEFAULT_LEAD_DAYS
    pair = logistics_index.get(norm_key)
    if pair is None:
        return _DEFAULT_LEAD_DAYS
    long_msk, long_rostov = pair
    return int(long_msk) + _CUSTOMS_DAYS + int(long_rostov)


def plan_demand_for_month(row: Any, month: str) -> float:
    """Σ план по категориям из monthly_demand."""
    demand = getattr(row, "monthly_demand", None) or {}
    bucket = demand.get(month) or {}
    total = 0.0
    for category in _SCHEDULE_CATEGORIES:
        cat = bucket.get(category) or {}
        if isinstance(cat, dict):
            total += float(cat.get("план", 0.0) or 0.0)
        elif isinstance(cat, (int, float)):
            total += float(cat)
    return total


def compute_order_plan(
    merged: Iterable[Any],
    months: list[str],
    year: int,
    logistics_index: Mapping[str, tuple[int, int]] | None = None,
) -> OrderPlanResult:
    """Дата = 1-е M − lead; qty = скользящий дефицит к плановой потребности."""
    from app.agents.document_analysis_agent.product_coverage import _normalize

    rows = list(merged)
    nomenclatures = [
        str(getattr(row, "nomenclature", "") or "").strip()
        for row in rows
        if str(getattr(row, "nomenclature", "") or "").strip()
    ]
    cells: dict[tuple[str, str], OrderPlanCell] = {}
    leads: dict[str, int] = {}

    if not months:
        return OrderPlanResult(
            months=[],
            nomenclatures=nomenclatures,
            year=year,
            cells=cells,
            leads=leads,
        )

    for row in rows:
        display = str(getattr(row, "nomenclature", "") or "").strip()
        if not display:
            continue
        key = _normalize(display)
        lead = lead_days_for(key, logistics_index)
        leads[key] = lead

        stock_val = getattr(row, "stock", None)
        opening = 0.0 if stock_val is None else max(0.0, float(stock_val))
        receipts_map = getattr(row, "monthly_receipts", None) or {}

        for month in months:
            anchor = first_day_of_month(year, month)
            if anchor is None:
                # месяц вне календаря — пропускаем ячейку
                continue
            order_date = anchor - timedelta(days=lead)
            receipt = max(0.0, float(receipts_map.get(month, 0.0) or 0.0))
            available = opening + receipt
            demand = plan_demand_for_month(row, month)
            order_qty = max(0.0, demand - available)
            opening = available + order_qty - demand  # >= 0 when order_qty covers deficit

            cells[(display, month)] = OrderPlanCell(
                month=month,
                order_date=order_date,
                qty=order_qty,
            )

    nonzero = sum(1 for cell in cells.values() if cell.qty > 0)
    logger.info(
        "document_analysis_agent.order_plan_computed",
        nomenclatures=len(nomenclatures),
        months=months,
        year=year,
        logistics_known=len(logistics_index or {}),
        nonzero_order_cells=nonzero,
    )
    return OrderPlanResult(
        months=list(months),
        nomenclatures=nomenclatures,
        year=year,
        cells=cells,
        leads=leads,
    )
