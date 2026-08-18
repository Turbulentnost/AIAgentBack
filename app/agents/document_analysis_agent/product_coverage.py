"""Обеспеченность изделий по месяцам: сборка из материалов с пропорциональным α + добор сверху вниз."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import structlog

from app.agents.document_analysis_agent.material_classification import (
    MATERIAL_KIND_REQUIRED,
    is_optional_material_kind,
    is_zero_supply_optional_material,
)

logger = structlog.get_logger(__name__)

_SCHEDULE_CATEGORIES = ("заказ", "опытные", "склад")
_WS_RE = re.compile(r"\s+")
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


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower().replace("ё", "е")
    text = text.replace('"', "").replace("«", "").replace("»", "").replace("'", "")
    return _WS_RE.sub(" ", text)


def _month_index(name: str) -> int:
    try:
        return _MONTH_ORDER.index(name)
    except ValueError:
        return 99


@dataclass
class ProductBomLine:
    """Строка BOM для раскрытия изделия на листе обеспеченности."""

    nomenclature: str
    norm_key: str
    qty_per_unit: float
    material_kind: str = MATERIAL_KIND_REQUIRED
    material_kind_label: str = ""
    material_kind_confidence: str = ""
    material_kind_reason: str = ""


@dataclass
class ProductBom:
    product: str
    materials: dict[str, float] = field(default_factory=dict)  # norm_name → qty per unit
    material_names: dict[str, str] = field(default_factory=dict)  # norm → display
    material_kinds: dict[str, str] = field(default_factory=dict)
    material_kind_labels: dict[str, str] = field(default_factory=dict)
    material_kind_confidences: dict[str, str] = field(default_factory=dict)
    material_kind_reasons: dict[str, str] = field(default_factory=dict)
    matched: bool = False

    def lines(self) -> list[ProductBomLine]:
        items = [
            ProductBomLine(
                nomenclature=self.material_names.get(key, key),
                norm_key=key,
                qty_per_unit=qty,
                material_kind=self.material_kinds.get(key, MATERIAL_KIND_REQUIRED),
                material_kind_label=self.material_kind_labels.get(key, ""),
                material_kind_confidence=self.material_kind_confidences.get(key, ""),
                material_kind_reason=self.material_kind_reasons.get(key, ""),
            )
            for key, qty in self.materials.items()
            if qty > 0
        ]
        items.sort(key=lambda line: _normalize(line.nomenclature))
        return items


@dataclass
class ProductMonthCoverage:
    product: str
    month: str
    plan: float
    covered: float
    fact: float = 0.0
    cover_ratio: float = 0.0
    conditional_covered: float = 0.0
    conditional_cover_ratio: float = 0.0
    limiting_materials: list[str] = field(default_factory=list)


@dataclass
class ProductCoverageResult:
    months: list[str]
    products_in_order: list[str]
    boms: dict[str, ProductBom]
    cells: dict[tuple[str, str], ProductMonthCoverage] = field(default_factory=dict)
    # month → norm_key → available (opening + receipts) до списания covered
    month_available: dict[str, dict[str, float]] = field(default_factory=dict)

    def cell(self, product: str, month: str) -> ProductMonthCoverage:
        return self.cells.get(
            (product, month),
            ProductMonthCoverage(product=product, month=month, plan=0.0, covered=0.0, fact=0.0),
        )

    def material_plan(self, product: str, month: str, norm_key: str) -> float:
        """Потребность материала на план изделия в месяце = план × qty из спеки."""
        bom = self.boms.get(product)
        if bom is None:
            return 0.0
        qty = float(bom.materials.get(norm_key, 0.0) or 0.0)
        if qty <= 0:
            return 0.0
        return float(self.cell(product, month).plan) * qty

    def material_fact(self, product: str, month: str, norm_key: str) -> float:
        """Потребность материала на факт изделия в месяце = факт × qty из спеки."""
        bom = self.boms.get(product)
        if bom is None:
            return 0.0
        qty = float(bom.materials.get(norm_key, 0.0) or 0.0)
        if qty <= 0:
            return 0.0
        return float(self.cell(product, month).fact) * qty

    def material_available(self, month: str, norm_key: str) -> float:
        return float((self.month_available.get(month) or {}).get(norm_key, 0.0) or 0.0)


def plan_total_for_month(plan: Any, month: str) -> float:
    """Σ план по категориям заказ/опытные/склад для изделия в месяце."""
    monthly = getattr(plan, "monthly_qty", None) or {}
    bucket = monthly.get(month) or {}
    total = 0.0
    for category in _SCHEDULE_CATEGORIES:
        total += float((bucket.get(category) or {}).get("план", 0.0) or 0.0)
    return total


def fact_total_for_month(plan: Any, month: str) -> float:
    """Σ факт по категориям заказ/опытные/склад для изделия в месяце."""
    monthly = getattr(plan, "monthly_qty", None) or {}
    bucket = monthly.get(month) or {}
    total = 0.0
    for category in _SCHEDULE_CATEGORIES:
        total += float((bucket.get(category) or {}).get("факт", 0.0) or 0.0)
    return total


def build_boms_from_merged(
    products_in_order: list[str],
    merged: Iterable[Any],
) -> dict[str, ProductBom]:
    """BOM: изделие → {norm(материал): qty на 1 изделие} из MergedNomenclatureRow.by_product."""
    from app.agents.document_analysis_agent.excel_service import _match_key

    boms: dict[str, ProductBom] = {
        product: ProductBom(product=product, matched=False) for product in products_in_order
    }
    product_keys = {_normalize(p): p for p in products_in_order}
    product_match_keys = {_match_key(p): p for p in products_in_order if _match_key(p)}

    for row in merged:
        by_product = getattr(row, "by_product", None) or {}
        if not by_product:
            continue
        display = str(getattr(row, "nomenclature", "") or "").strip()
        mat_key = _normalize(display)
        if not mat_key:
            continue
        for product_name, qty in by_product.items():
            canonical = product_keys.get(_normalize(product_name))
            if canonical is None:
                canonical = product_match_keys.get(_match_key(product_name))
            if canonical is None:
                continue
            if qty is None:
                continue
            q = float(qty)
            if q <= 0:
                continue
            bom = boms[canonical]
            bom.materials[mat_key] = bom.materials.get(mat_key, 0.0) + q
            if display and mat_key not in bom.material_names:
                bom.material_names[mat_key] = display
            kind = _row_material_meta(row, product_name, canonical, "kind")
            label = _row_material_meta(row, product_name, canonical, "label")
            confidence = _row_material_meta(row, product_name, canonical, "confidence")
            reason = _row_material_meta(row, product_name, canonical, "reason")
            if mat_key not in bom.material_kinds or is_optional_material_kind(kind):
                bom.material_kinds[mat_key] = kind or MATERIAL_KIND_REQUIRED
                bom.material_kind_labels[mat_key] = label or ""
                bom.material_kind_confidences[mat_key] = confidence or ""
                bom.material_kind_reasons[mat_key] = reason or ""
            bom.matched = True

    return boms


def _row_material_meta(row: Any, product_name: str, canonical: str, suffix: str) -> str:
    by_product = getattr(row, f"coverage_material_{suffix}s_by_product", None) or {}
    if by_product:
        for key in (product_name, canonical):
            if key in by_product:
                return str(by_product.get(key) or "")
        norm_index = {_normalize(key): value for key, value in by_product.items()}
        for key in (product_name, canonical):
            value = norm_index.get(_normalize(key))
            if value:
                return str(value)
    return str(getattr(row, f"coverage_material_{suffix}", "") or "")


def _relaxed_boms_for_period(
    boms: dict[str, ProductBom],
    available: dict[str, float],
) -> dict[str, ProductBom]:
    """BOM для условной обеспеченности: без спорных позиций с нулевым остатком+поступлением."""
    relaxed: dict[str, ProductBom] = {}
    for product, bom in boms.items():
        kept = {
            mat: qty
            for mat, qty in bom.materials.items()
            if not is_zero_supply_optional_material(
                bom.material_kinds.get(mat),
                available=float(available.get(mat, 0.0) or 0.0),
            )
        }
        relaxed[product] = ProductBom(
            product=bom.product,
            materials=kept,
            material_names={
                mat: name for mat, name in bom.material_names.items() if mat in kept
            },
            material_kinds={
                mat: kind for mat, kind in bom.material_kinds.items() if mat in kept
            },
            material_kind_labels={
                mat: label for mat, label in bom.material_kind_labels.items() if mat in kept
            },
            material_kind_confidences={
                mat: confidence
                for mat, confidence in bom.material_kind_confidences.items()
                if mat in kept
            },
            material_kind_reasons={
                mat: reason
                for mat, reason in bom.material_kind_reasons.items()
                if mat in kept
            },
            matched=bom.matched,
        )
    return relaxed


def _relaxed_boms_without_optional_materials(
    boms: dict[str, ProductBom],
) -> dict[str, ProductBom]:
    """Legacy alias: все спорные позиции считаются без остатка."""
    zero_available = {
        mat: 0.0 for bom in boms.values() for mat in bom.materials if is_optional_material_kind(
            bom.material_kinds.get(mat)
        )
    }
    return _relaxed_boms_for_period(boms, zero_available)


def _material_supply_maps(
    merged: Iterable[Any],
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Остатки и помесячные поступления по нормализованному имени."""
    stock: dict[str, float] = {}
    receipts: dict[str, dict[str, float]] = {}
    for row in merged:
        key = _normalize(getattr(row, "nomenclature", ""))
        if not key:
            continue
        stock_val = getattr(row, "stock", None)
        stock[key] = 0.0 if stock_val is None else float(stock_val)
        row_receipts = getattr(row, "monthly_receipts", None) or {}
        receipts[key] = {month: float(qty) for month, qty in row_receipts.items()}
    return stock, receipts


def _seed_opening_with_pre_horizon_receipts(
    opening: dict[str, float],
    receipts_map: dict[str, dict[str, float]],
    keys: set[str],
    first_month: str,
) -> dict[str, float]:
    """Поступления до первого месяца горизонта входят в opening (как уже на складе)."""
    first_idx = _month_index(first_month)
    seeded = dict(opening)
    for key in keys:
        extra = 0.0
        for month, qty in (receipts_map.get(key) or {}).items():
            if _month_index(month) < first_idx:
                extra += max(0.0, float(qty))
        if extra:
            seeded[key] = max(0.0, float(seeded.get(key, 0.0))) + extra
    return seeded


def _bom_mats(bom: ProductBom) -> dict[str, float]:
    """Все позиции спецификации с qty > 0 — каждая обязательна для сборки."""
    return {mat: qty for mat, qty in bom.materials.items() if qty > 0}


def _can_build_one(bom: ProductBom, pool: dict[str, float]) -> bool:
    for mat, qty in _bom_mats(bom).items():
        if pool.get(mat, 0.0) + 1e-9 < qty:
            return False
    return True


def _consume(bom: ProductBom, pool: dict[str, float], units: float) -> None:
    if units <= 0:
        return
    for mat, qty in _bom_mats(bom).items():
        pool[mat] = pool.get(mat, 0.0) - units * qty


def _pass_consumption(
    products: list[str],
    boms: dict[str, ProductBom],
) -> dict[str, float]:
    consumption: dict[str, float] = {}
    for product in products:
        for mat, qty in _bom_mats(boms[product]).items():
            consumption[mat] = consumption.get(mat, 0.0) + qty
    return consumption


def _max_repeated_successful_passes(
    products: list[str],
    plan_m: dict[str, float],
    boms: dict[str, ProductBom],
    covered: dict[str, float],
    pool: dict[str, float],
) -> int:
    """How many additional identical round-robin passes can run after one pass.

    The greedy top-up is intentionally order-dependent. We keep that behavior,
    but when a whole pass succeeds for the same set of products we can apply
    the repeated passes in bulk instead of looping one unit at a time.
    """
    if not products:
        return 0
    repeat_limit = math.inf
    for product in products:
        remaining = math.floor(plan_m[product] - covered.get(product, 0.0) + 1e-9)
        repeat_limit = min(repeat_limit, remaining)
    for mat, qty in _pass_consumption(products, boms).items():
        if qty <= 1e-12:
            continue
        repeat_limit = min(repeat_limit, math.floor(pool.get(mat, 0.0) / qty + 1e-9))
    if math.isinf(repeat_limit):
        return 0
    return max(0, int(repeat_limit))


def _limiting_materials(
    bom: ProductBom,
    pool: dict[str, float],
    plan: float,
) -> list[str]:
    """Материалы спеки, которые сильнее всего ограничивают полный plan."""
    if plan <= 0:
        return []
    ranked: list[tuple[float, str]] = []
    for mat, qty in _bom_mats(bom).items():
        need = plan * qty
        if need <= 0:
            continue
        avail = pool.get(mat, 0.0)
        ranked.append((avail / need, mat))
    ranked.sort(key=lambda item: item[0])
    return [mat for ratio, mat in ranked[:3] if ratio < 1.0 - 1e-9]


def _materials_in_play(active: list[str], boms: dict[str, ProductBom]) -> set[str]:
    keys: set[str] = set()
    for product in active:
        keys.update(_bom_mats(boms[product]))
    return keys


def _alpha_for_active(
    active: list[str],
    plan_m: dict[str, float],
    boms: dict[str, ProductBom],
    available: dict[str, float],
) -> float:
    alpha = 1.0
    for mat in _materials_in_play(active, boms):
        need = 0.0
        for product in active:
            need += plan_m[product] * boms[product].materials.get(mat, 0.0)
        if need > 1e-12:
            alpha = min(alpha, available.get(mat, 0.0) / need)
    return max(0.0, min(1.0, alpha))


def _products_blocked_by_zero_supply(
    active: list[str],
    plan_m: dict[str, float],
    boms: dict[str, ProductBom],
    available: dict[str, float],
) -> set[str]:
    """Изделия, которым нужен материал спеки с available≈0."""
    zero_mats = {
        mat
        for mat in _materials_in_play(active, boms)
        if available.get(mat, 0.0) <= 1e-12
        and any(plan_m[p] * boms[p].materials.get(mat, 0.0) > 1e-12 for p in active)
    }
    if not zero_mats:
        return set()
    blocked: set[str] = set()
    for product in active:
        for mat in zero_mats:
            if boms[product].materials.get(mat, 0.0) > 0:
                blocked.add(product)
                break
    return blocked


def _select_active_for_alpha(
    candidates: list[str],
    plan_m: dict[str, float],
    boms: dict[str, ProductBom],
    available: dict[str, float],
) -> list[str]:
    """Убирает изделия с нулевым дефицитом по своей спеке, чтобы не обнулять общий α."""
    active = list(candidates)
    while active:
        alpha = _alpha_for_active(active, plan_m, boms, available)
        if alpha > 1e-15:
            return active
        blocked = _products_blocked_by_zero_supply(active, plan_m, boms, available)
        if not blocked:
            return active
        active = [product for product in active if product not in blocked]
    return []


def _compute_covered_for_candidates(
    candidates: list[str],
    plan_m: dict[str, float],
    boms: dict[str, ProductBom],
    available: dict[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    active = _select_active_for_alpha(candidates, plan_m, boms, available)

    alpha = _alpha_for_active(active, plan_m, boms, available) if active else 0.0
    covered: dict[str, float] = {
        product: float(math.floor(alpha * plan_m[product] + 1e-9)) for product in active
    }

    pool = dict(available)
    for product in active:
        _consume(boms[product], pool, covered[product])

    changed = True
    safety = 0
    max_iters = int(sum(plan_m[p] for p in candidates)) + len(candidates) + 10
    while changed and safety < max_iters:
        changed = False
        safety += 1
        pass_products: list[str] = []
        for product in candidates:
            current = covered.get(product, 0.0)
            if current + 1 <= plan_m[product] + 1e-9 and _can_build_one(
                boms[product], pool
            ):
                covered[product] = current + 1
                _consume(boms[product], pool, 1)
                pass_products.append(product)
                changed = True
        if not pass_products:
            continue
        repeated = _max_repeated_successful_passes(
            pass_products,
            plan_m,
            boms,
            covered,
            pool,
        )
        if repeated <= 0:
            continue
        pass_usage = _pass_consumption(pass_products, boms)
        for product in pass_products:
            covered[product] = covered.get(product, 0.0) + repeated
        for mat, qty in pass_usage.items():
            pool[mat] = pool.get(mat, 0.0) - repeated * qty

    return covered, pool


def compute_product_coverage(
    schedule_plans: list[Any],
    merged: list[Any],
    months: list[str],
) -> ProductCoverageResult:
    """Считает обеспеченность: изделие собрано, только если хватает ВСЕХ позиций спеки.

    Доступно по материалу = остаток на начало месяца + поступления месяца.
    Нет в остатках/отгрузках → доступно 0 → позиция блокирует сборку.
    """
    products_in_order = [plan.product for plan in schedule_plans]
    plans_by_product = {plan.product: plan for plan in schedule_plans}
    boms = build_boms_from_merged(products_in_order, merged)
    stock0, receipts_map = _material_supply_maps(merged)

    bom_keys = {key for bom in boms.values() for key in bom.materials}
    opening: dict[str, float] = {key: float(stock0.get(key, 0.0)) for key in bom_keys}
    if months:
        opening = _seed_opening_with_pre_horizon_receipts(
            opening, receipts_map, bom_keys, months[0]
        )

    cells: dict[tuple[str, str], ProductMonthCoverage] = {}
    month_available: dict[str, dict[str, float]] = {}

    if not months:
        return ProductCoverageResult(
            months=[],
            products_in_order=products_in_order,
            boms=boms,
            cells=cells,
            month_available=month_available,
        )

    for month in months:
        available: dict[str, float] = {
            key: max(0.0, float(opening.get(key, 0.0)))
            + max(0.0, float((receipts_map.get(key) or {}).get(month, 0.0)))
            for key in bom_keys
        }
        relaxed_available: dict[str, float] = dict(available)
        month_available[month] = dict(available)

        plan_m: dict[str, float] = {
            product: plan_total_for_month(plans_by_product[product], month)
            for product in products_in_order
            if product in plans_by_product
        }
        fact_m: dict[str, float] = {
            product: fact_total_for_month(plans_by_product[product], month)
            for product in products_in_order
            if product in plans_by_product
        }
        candidates = [
            product
            for product in products_in_order
            if plan_m.get(product, 0.0) > 0 and boms.get(product, ProductBom(product)).matched
        ]
        covered, pool = _compute_covered_for_candidates(candidates, plan_m, boms, available)
        relaxed_boms = _relaxed_boms_for_period(boms, available)
        conditional_covered, _relaxed_pool = _compute_covered_for_candidates(
            candidates,
            plan_m,
            relaxed_boms,
            relaxed_available,
        )

        for product in products_in_order:
            plan = float(plan_m.get(product, 0.0))
            fact = float(fact_m.get(product, 0.0))
            cov = float(covered.get(product, 0.0))
            conditional_cov = max(cov, float(conditional_covered.get(product, 0.0)))
            bom = boms.get(product) or ProductBom(product=product)
            limiting: list[str] = []
            if plan > 0 and bom.matched and cov + 1e-9 < plan:
                limiting = _limiting_materials(bom, available, plan)
            ratio = (cov / plan) if plan > 0 else 0.0
            conditional_ratio = (conditional_cov / plan) if plan > 0 else 0.0
            cells[(product, month)] = ProductMonthCoverage(
                product=product,
                month=month,
                plan=plan,
                fact=fact,
                covered=cov,
                cover_ratio=ratio,
                conditional_covered=conditional_cov,
                conditional_cover_ratio=conditional_ratio,
                limiting_materials=limiting,
            )

        opening = {key: max(0.0, float(pool.get(key, 0.0))) for key in bom_keys}

    fully_covered = sum(
        1
        for product in products_in_order
        for month in months[:1]
        if cells.get((product, month))
        and cells[(product, month)].plan > 0
        and cells[(product, month)].covered + 1e-9 >= cells[(product, month)].plan
    )
    logger.info(
        "document_analysis_agent.product_coverage_computed",
        products=len(products_in_order),
        months=months,
        matched_boms=sum(1 for bom in boms.values() if bom.matched),
        bom_materials=len(bom_keys),
        first_month_fully_covered=fully_covered,
    )
    return ProductCoverageResult(
        months=list(months),
        products_in_order=products_in_order,
        boms=boms,
        cells=cells,
        month_available=month_available,
    )


@dataclass
class ProductDayCoverage:
    product: str
    day: str
    plan: float
    covered: float
    fact: float = 0.0
    cover_ratio: float = 0.0
    conditional_covered: float = 0.0
    conditional_cover_ratio: float = 0.0
    matched: bool = False


@dataclass
class DailyPlanCoverageResult:
    """Обеспеченность дневного плана П/ф: (product, day_iso) → covered."""

    day_keys: list[str]
    products_in_order: list[str]
    boms: dict[str, ProductBom]
    cells: dict[tuple[str, str], ProductDayCoverage] = field(default_factory=dict)

    def cell(self, product: str, day: str) -> ProductDayCoverage:
        return self.cells.get(
            (product, day),
            ProductDayCoverage(product=product, day=day, plan=0.0, covered=0.0, fact=0.0),
        )

    def covered_for_days(self, product: str, day_keys: list[str]) -> float:
        return sum(float(self.cell(product, day).covered) for day in day_keys)

    def conditional_covered_for_days(self, product: str, day_keys: list[str]) -> float:
        total = 0.0
        for day in day_keys:
            cell = self.cell(product, day)
            total += max(float(cell.covered or 0.0), float(cell.conditional_covered or 0.0))
        return total

    def status_for_plan_cell(
        self,
        product: str,
        day_keys: list[str],
        plan_qty: float,
    ) -> str | None:
        """green / yellow / red / None (без заливки при plan<=0)."""
        if plan_qty is None or float(plan_qty) <= 1e-12:
            return None
        plan = float(plan_qty)
        bom = self.boms.get(product)
        matched = bool(bom and bom.matched)
        covered = float(self.covered_for_days(product, day_keys))
        conditional = float(self.conditional_covered_for_days(product, day_keys))
        if not matched or covered <= 1e-12:
            return "yellow" if conditional > 1e-12 else "red"
        if covered + 1e-9 < plan or conditional + 1e-9 < plan:
            return "yellow"
        return "green"


def _material_daily_supply_maps(
    merged: Iterable[Any],
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Остатки и дневные поступления по нормализованному имени."""
    stock: dict[str, float] = {}
    receipts: dict[str, dict[str, float]] = {}
    for row in merged:
        key = _normalize(getattr(row, "nomenclature", ""))
        if not key:
            continue
        stock_val = getattr(row, "stock", None)
        stock[key] = 0.0 if stock_val is None else float(stock_val)
        row_receipts = getattr(row, "daily_receipts", None) or {}
        receipts[key] = {day: float(qty) for day, qty in row_receipts.items()}
    return stock, receipts


def _seed_opening_with_pre_horizon_daily_receipts(
    opening: dict[str, float],
    receipts_map: dict[str, dict[str, float]],
    keys: set[str],
    first_day: str,
) -> dict[str, float]:
    seeded = dict(opening)
    for key in keys:
        extra = 0.0
        for day, qty in (receipts_map.get(key) or {}).items():
            if day < first_day:
                extra += max(0.0, float(qty))
        if extra:
            seeded[key] = max(0.0, float(seeded.get(key, 0.0))) + extra
    return seeded


def _catalog_products_from_merged(merged: Iterable[Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in merged:
        by_product = getattr(row, "by_product", None) or {}
        for product_name in by_product:
            key = _normalize(product_name)
            if not key or key in seen:
                continue
            seen.add(key)
            names.append(str(product_name))
    return names


def _detailed_name_aliases(key: str) -> list[str]:
    """Варианты коротких имён Отчёта → ключи для поиска в каталоге спеки."""
    aliases = [key]
    # «Сокол ИТ» ≈ ночной «… СОКОЛ Т»; «Сокол ИСТ» ≈ «… ИС -Т»
    if key.endswith(" ист"):
        base = key[: -len(" ист")].rstrip()
        aliases.extend(
            [
                f"{base} ис -т",
                f"{base} ис-т",
                f"{base} ис т",
            ]
        )
    if key.endswith(" ит"):
        base = key[: -len(" ит")].rstrip()
        aliases.append(f"{base} т")
    if " z40" in key or "(z40)" in key or key.endswith(" z40"):
        aliases.append(re.sub(r"\(?\s*z\s*-?\s*40\s*\)?", " z40", key))
    if re.search(r"\bр\b", key) and " z40" in key:
        aliases.append(re.sub(r"\bsokol\b|\bсокол\b", "сокол", key))
    # уникальные, длинные раньше коротких
    seen: set[str] = set()
    ordered: list[str] = []
    for item in aliases:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _alias_fits_candidate(alias: str, cand_key: str) -> bool:
    """alias входит в cand как отдельная фраза (не префикс «сокол и» ⊂ «сокол ис»)."""
    if not alias or alias not in cand_key:
        return False
    start = 0
    while True:
        idx = cand_key.find(alias, start)
        if idx < 0:
            return False
        after = idx + len(alias)
        before_ok = idx == 0 or not cand_key[idx - 1].isalnum()
        after_ok = after >= len(cand_key) or not cand_key[after].isalnum()
        if before_ok and after_ok:
            return True
        start = idx + 1


def _match_detailed_to_catalog(detailed: str, catalog: list[str]) -> str | None:
    """Сопоставляет короткое имя детального плана с изделием из BOM/спеки."""
    if not detailed or not catalog:
        return None
    from app.agents.document_analysis_agent.excel_service import _match_key

    key = _normalize(detailed)
    by_key = {_normalize(name): name for name in catalog}
    for alias in _detailed_name_aliases(key):
        if alias in by_key:
            return by_key[alias]

    detailed_mk = _match_key(detailed)
    by_match_key = {_match_key(name): name for name in catalog if _match_key(name)}
    if detailed_mk and detailed_mk in by_match_key:
        return by_match_key[detailed_mk]

    best_name: str | None = None
    best_len = -1
    for alias in _detailed_name_aliases(key):
        for cand_key, cand_name in by_key.items():
            if _alias_fits_candidate(alias, cand_key) and len(alias) > best_len:
                best_name = cand_name
                best_len = len(alias)
            elif _alias_fits_candidate(cand_key, alias) and len(cand_key) > best_len:
                best_name = cand_name
                best_len = len(cand_key)
    if best_name is not None:
        return best_name

    for alias in _detailed_name_aliases(key):
        key_tokens = [t for t in alias.replace("-", " ").split() if len(t) >= 2]
        if len(key_tokens) < 2:
            continue
        for cand_key, cand_name in by_key.items():
            cand_tokens = cand_key.replace("-", " ")
            if all(token in cand_tokens.split() or token in cand_tokens for token in key_tokens):
                # требуем word-boundary для каждого токена длины >= 2
                if all(_alias_fits_candidate(token, cand_tokens) for token in key_tokens):
                    if len(key_tokens) > best_len:
                        best_name = cand_name
                        best_len = len(key_tokens)
    return best_name


def build_boms_for_detailed_products(
    detailed_products: list[str],
    merged: Iterable[Any],
) -> dict[str, ProductBom]:
    """BOM под именами детального графика (через матч к by_product спеки)."""
    catalog = _catalog_products_from_merged(merged)
    schedule_boms = build_boms_from_merged(catalog, merged)
    result: dict[str, ProductBom] = {}
    for detailed in detailed_products:
        matched = _match_detailed_to_catalog(detailed, catalog)
        if matched is None:
            result[detailed] = ProductBom(product=detailed, matched=False)
            continue
        source = schedule_boms.get(matched) or ProductBom(product=matched, matched=False)
        result[detailed] = ProductBom(
            product=detailed,
            materials=dict(source.materials),
            material_names=dict(source.material_names),
            material_kinds=dict(source.material_kinds),
            material_kind_labels=dict(source.material_kind_labels),
            material_kind_confidences=dict(source.material_kind_confidences),
            material_kind_reasons=dict(source.material_kind_reasons),
            matched=source.matched,
        )
    return result


def compute_daily_plan_coverage(
    detailed_plans: list[Any],
    merged: list[Any],
    day_keys: list[str],
) -> DailyPlanCoverageResult:
    """Дневная обеспеченность П/ф: остаток + поступления дня, fair share, списание на завтра."""
    products_in_order = [str(getattr(plan, "product", "") or "") for plan in detailed_plans]
    products_in_order = [name for name in products_in_order if name]
    plans_by_product = {
        str(getattr(plan, "product", "") or ""): plan for plan in detailed_plans
    }
    boms = build_boms_for_detailed_products(products_in_order, merged)
    stock0, receipts_map = _material_daily_supply_maps(merged)

    bom_keys = {key for bom in boms.values() for key in bom.materials}
    opening: dict[str, float] = {key: float(stock0.get(key, 0.0)) for key in bom_keys}
    if day_keys:
        opening = _seed_opening_with_pre_horizon_daily_receipts(
            opening, receipts_map, bom_keys, day_keys[0]
        )

    cells: dict[tuple[str, str], ProductDayCoverage] = {}
    if not day_keys:
        return DailyPlanCoverageResult(
            day_keys=[],
            products_in_order=products_in_order,
            boms=boms,
            cells=cells,
        )

    for day in day_keys:
        available: dict[str, float] = {
            key: max(0.0, float(opening.get(key, 0.0)))
            + max(0.0, float((receipts_map.get(key) or {}).get(day, 0.0)))
            for key in bom_keys
        }
        relaxed_available: dict[str, float] = dict(available)

        plan_d: dict[str, float] = {}
        fact_d: dict[str, float] = {}
        for product in products_in_order:
            plan = plans_by_product.get(product)
            daily_qty = getattr(plan, "daily_qty", None) or {}
            daily_fact = getattr(plan, "daily_fact", None) or {}
            plan_d[product] = float(daily_qty.get(day, 0.0) or 0.0)
            fact_d[product] = float(daily_fact.get(day, 0.0) or 0.0)

        candidates = [
            product
            for product in products_in_order
            if plan_d.get(product, 0.0) > 0 and boms.get(product, ProductBom(product)).matched
        ]
        covered, pool = _compute_covered_for_candidates(candidates, plan_d, boms, available)
        relaxed_boms = _relaxed_boms_for_period(boms, available)
        conditional_covered, _relaxed_pool = _compute_covered_for_candidates(
            candidates,
            plan_d,
            relaxed_boms,
            relaxed_available,
        )

        for product in products_in_order:
            plan = float(plan_d.get(product, 0.0))
            cov = float(covered.get(product, 0.0))
            conditional_cov = max(cov, float(conditional_covered.get(product, 0.0)))
            bom = boms.get(product) or ProductBom(product=product)
            ratio = (cov / plan) if plan > 0 else 0.0
            conditional_ratio = (conditional_cov / plan) if plan > 0 else 0.0
            cells[(product, day)] = ProductDayCoverage(
                product=product,
                day=day,
                plan=plan,
                covered=cov,
                fact=float(fact_d.get(product, 0.0)),
                cover_ratio=ratio,
                conditional_covered=conditional_cov,
                conditional_cover_ratio=conditional_ratio,
                matched=bom.matched,
            )

        opening = {key: max(0.0, float(pool.get(key, 0.0))) for key in bom_keys}

    logger.info(
        "document_analysis_agent.daily_plan_coverage_computed",
        products=len(products_in_order),
        days=len(day_keys),
        matched_boms=sum(1 for bom in boms.values() if bom.matched),
        colored_plan_days=sum(
            1 for cell in cells.values() if cell.plan > 0
        ),
    )
    return DailyPlanCoverageResult(
        day_keys=list(day_keys),
        products_in_order=products_in_order,
        boms=boms,
        cells=cells,
    )
