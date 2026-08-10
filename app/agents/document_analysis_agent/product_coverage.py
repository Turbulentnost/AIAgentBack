"""Обеспеченность изделий по месяцам: сборка из материалов с пропорциональным α + добор сверху вниз."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import structlog

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


@dataclass
class ProductBom:
    product: str
    materials: dict[str, float] = field(default_factory=dict)  # norm_name → qty per unit
    material_names: dict[str, str] = field(default_factory=dict)  # norm → display
    matched: bool = False

    def lines(self) -> list[ProductBomLine]:
        items = [
            ProductBomLine(
                nomenclature=self.material_names.get(key, key),
                norm_key=key,
                qty_per_unit=qty,
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
    boms: dict[str, ProductBom] = {
        product: ProductBom(product=product, matched=False) for product in products_in_order
    }
    product_keys = {_normalize(p): p for p in products_in_order}

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
            bom.matched = True

    return boms


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
            for product in candidates:
                current = covered.get(product, 0.0)
                if current + 1 <= plan_m[product] + 1e-9 and _can_build_one(
                    boms[product], pool
                ):
                    covered[product] = current + 1
                    _consume(boms[product], pool, 1)
                    changed = True

        for product in products_in_order:
            plan = float(plan_m.get(product, 0.0))
            fact = float(fact_m.get(product, 0.0))
            cov = float(covered.get(product, 0.0))
            bom = boms.get(product) or ProductBom(product=product)
            limiting: list[str] = []
            if plan > 0 and bom.matched and cov + 1e-9 < plan:
                limiting = _limiting_materials(bom, available, plan)
            ratio = (cov / plan) if plan > 0 else 0.0
            cells[(product, month)] = ProductMonthCoverage(
                product=product,
                month=month,
                plan=plan,
                fact=fact,
                covered=cov,
                cover_ratio=ratio,
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
        if not matched or covered <= 1e-12:
            return "red"
        if covered + 1e-9 < plan:
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
    key = _normalize(detailed)
    by_key = {_normalize(name): name for name in catalog}
    for alias in _detailed_name_aliases(key):
        if alias in by_key:
            return by_key[alias]

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
        active = _select_active_for_alpha(candidates, plan_d, boms, available)

        alpha = _alpha_for_active(active, plan_d, boms, available) if active else 0.0
        covered: dict[str, float] = {
            product: float(math.floor(alpha * plan_d[product] + 1e-9)) for product in active
        }

        pool = dict(available)
        for product in active:
            _consume(boms[product], pool, covered[product])

        changed = True
        safety = 0
        max_iters = int(sum(plan_d[p] for p in candidates)) + len(candidates) + 10
        while changed and safety < max_iters:
            changed = False
            safety += 1
            for product in candidates:
                current = covered.get(product, 0.0)
                if current + 1 <= plan_d[product] + 1e-9 and _can_build_one(
                    boms[product], pool
                ):
                    covered[product] = current + 1
                    _consume(boms[product], pool, 1)
                    changed = True

        for product in products_in_order:
            plan = float(plan_d.get(product, 0.0))
            cov = float(covered.get(product, 0.0))
            bom = boms.get(product) or ProductBom(product=product)
            ratio = (cov / plan) if plan > 0 else 0.0
            cells[(product, day)] = ProductDayCoverage(
                product=product,
                day=day,
                plan=plan,
                covered=cov,
                fact=float(fact_d.get(product, 0.0)),
                cover_ratio=ratio,
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
