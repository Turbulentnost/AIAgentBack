"""Остатки и ресурсные спецификации из PostgreSQL (синхронизация 1С) для агента Aveon."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.onec_resource_spec import OnecResourceSpec, OnecResourceSpecMaterial
from app.models.onec_nomenclature import OnecNomenclature
from app.models.onec_production_plan import OnecProductionPlanHeader, OnecProductionPlanItem
from app.models.onec_stock import OnecStockBalance
from app.services.onec_production_plan_resolver import resolve_year_production_plan
from app.services.spec_nomenclature_match import EMPTY_GUID

logger = get_logger(__name__)

_SPEC_KEY_FIELDS = (
    "Спецификация_Key",
    "РесурснаяСпецификация_Key",
    "СпецификацияПродукции_Key",
)


def _plan_item_specification(item: OnecProductionPlanItem) -> tuple[str, str]:
    """Спецификация строки плана: из колонок БД или из raw_json (legacy-синхронизация)."""
    spec_key = (getattr(item, "specification_key", None) or "").strip()
    spec_name = (getattr(item, "specification_name", None) or "").strip()
    if spec_key or spec_name:
        return spec_key, spec_name
    raw_text = (getattr(item, "raw_json", None) or "").strip()
    if not raw_text:
        return "", ""
    try:
        import json

        raw = json.loads(raw_text)
    except json.JSONDecodeError:
        return "", ""
    if not isinstance(raw, dict):
        return "", ""
    for field in _SPEC_KEY_FIELDS:
        value = raw.get(field)
        if value:
            spec_key = str(value).strip()
            break
    display = raw.get("Спецификация") or raw.get("Specification")
    if display and not spec_name:
        spec_name = str(display).strip()
    return spec_key, spec_name


def _plan_spec_priority(spec_key: str, spec_name: str) -> int:
    if spec_key and spec_name:
        return 3
    if spec_key:
        return 2
    if spec_name:
        return 1
    return 0


def _valid_spec_ref_key(ref_key: str) -> str:
    cleaned = (ref_key or "").strip()
    if not cleaned or cleaned.lower() == EMPTY_GUID:
        return ""
    return cleaned


def build_product_spec_hints(
    schedule_plans: list["ScheduleProductPlan"],
) -> dict[str, tuple[str, str]]:
    """Подсказки spec_name/spec_ref_key из плана 1С по точному и нормализованному имени изделия."""
    from app.agents.document_analysis_agent.excel_service import ScheduleProductPlan, _normalize

    hints: dict[str, tuple[str, str]] = {}
    priority_by_key: dict[str, int] = {}
    for plan in schedule_plans:
        spec_name = (plan.spec_name or "").strip()
        spec_ref_key = _valid_spec_ref_key(plan.spec_ref_key)
        if not spec_name and not spec_ref_key:
            continue
        payload = (spec_name, spec_ref_key)
        priority = _plan_spec_priority(spec_ref_key, spec_name)
        for key in (plan.product, _normalize(plan.product)):
            if not key:
                continue
            if key not in hints or priority > priority_by_key.get(key, 0):
                hints[key] = payload
                priority_by_key[key] = priority
    return hints


def lookup_product_spec_hint(
    product: str,
    hints: dict[str, tuple[str, str]] | None,
) -> tuple[str, str]:
    if not hints:
        return "", ""
    direct = hints.get(product)
    if direct:
        return direct
    from app.agents.document_analysis_agent.excel_service import _normalize

    return hints.get(_normalize(product), ("", ""))


@dataclass(frozen=True)
class DbSpecCatalogEntry:
    ref_key: str
    label: str
    description: str
    main_product_name: str
    code: str


@dataclass(frozen=True)
class DbNomenclatureCountryEntry:
    nomenclature: str
    country_of_origin: str
    code: str = ""
    ref_key: str = ""


@dataclass(frozen=True)
class DbNomenclatureUnitEntry:
    nomenclature: str
    unit: str
    code: str = ""
    ref_key: str = ""


async def build_stock_index_from_db(db: AsyncSession) -> dict[str, "StockEntry"]:
    """Агрегирует остатки по номенклатуре (сумма available по складам)."""
    from app.agents.document_analysis_agent.excel_service import StockEntry, _normalize

    rows = (await db.execute(select(OnecStockBalance))).scalars().all()
    index: dict[str, StockEntry] = {}
    for row in rows:
        name = (row.name or "").strip()
        if not name:
            continue
        key = _normalize(name)
        if not key:
            continue
        qty = float(row.available if row.available is not None else row.in_stock or 0)
        if key in index:
            prev = index[key].quantity or 0.0
            index[key].quantity = prev + qty
        else:
            index[key] = StockEntry(nomenclature=name, quantity=qty, ordered_qty=None)

    logger.info("document_analysis_agent.db_stock_loaded", unique=len(index), rows=len(rows))
    return index


async def build_nomenclature_indexes_from_db(
    db: AsyncSession,
) -> tuple[dict[str, DbNomenclatureCountryEntry], dict[str, DbNomenclatureUnitEntry]]:
    """Один SELECT по onec_nomenclature для стран и единиц измерения."""
    from app.agents.document_analysis_agent.excel_service import _normalize

    rows = (await db.execute(select(OnecNomenclature))).scalars().all()
    country_index: dict[str, DbNomenclatureCountryEntry] = {}
    unit_index: dict[str, DbNomenclatureUnitEntry] = {}
    for row in rows:
        name = (row.name or "").strip()
        if not name:
            continue
        key = _normalize(name)
        if not key:
            continue
        code = (row.code or "").strip()
        country_index[key] = DbNomenclatureCountryEntry(
            nomenclature=name,
            country_of_origin=(row.country_of_origin or "").strip(),
            code=code,
            ref_key=row.ref_key,
        )
        unit_index[key] = DbNomenclatureUnitEntry(
            nomenclature=name,
            unit=(row.unit or "").strip(),
            code=code,
            ref_key=row.ref_key,
        )
    logger.info(
        "document_analysis_agent.db_nomenclature_loaded",
        unique=len(country_index),
        with_country=sum(1 for item in country_index.values() if item.country_of_origin),
        with_unit=sum(1 for item in unit_index.values() if item.unit),
    )
    return country_index, unit_index


async def build_country_index_from_db(db: AsyncSession) -> dict[str, DbNomenclatureCountryEntry]:
    """Индекс страна происхождения по нормализованному имени номенклатуры."""
    country_index, _ = await build_nomenclature_indexes_from_db(db)
    return country_index


async def build_unit_index_from_db(db: AsyncSession) -> dict[str, DbNomenclatureUnitEntry]:
    """Индекс единицы измерения по нормализованному имени номенклатуры."""
    _, unit_index = await build_nomenclature_indexes_from_db(db)
    return unit_index


async def load_db_spec_catalog(db: AsyncSession) -> list[DbSpecCatalogEntry]:
    specs = (
        await db.execute(
            select(OnecResourceSpec)
            .where(OnecResourceSpec.is_folder.is_(False))
            .order_by(OnecResourceSpec.description, OnecResourceSpec.code)
        )
    ).scalars().all()
    catalog: list[DbSpecCatalogEntry] = []
    for spec in specs:
        description = (spec.description or "").strip()
        main_product = (spec.main_product_name or "").strip()
        label = main_product or description or (spec.code or "").strip() or spec.ref_key
        catalog.append(
            DbSpecCatalogEntry(
                ref_key=spec.ref_key,
                label=label,
                description=description,
                main_product_name=main_product,
                code=(spec.code or "").strip(),
            )
        )
    logger.info("document_analysis_agent.db_spec_catalog_loaded", count=len(catalog))
    return catalog


async def _load_resolved_production_plan_rows(
    db: AsyncSession,
    *,
    year: int | None = None,
) -> tuple[list[OnecProductionPlanHeader], list[OnecProductionPlanItem], "ResolvedYearProductionPlan"]:
    from datetime import date

    from app.services.onec_production_plan_resolver import ResolvedYearProductionPlan

    target_year = year or date.today().year
    headers = (
        await db.execute(
            select(OnecProductionPlanHeader).order_by(OnecProductionPlanHeader.plan_date.desc())
        )
    ).scalars().all()
    if not headers:
        return [], [], ResolvedYearProductionPlan(year=target_year)
    rows = (
        await db.execute(
            select(OnecProductionPlanItem).order_by(
                OnecProductionPlanItem.month_key,
                OnecProductionPlanItem.line_number,
                OnecProductionPlanItem.nomenclature_name,
            )
        )
    ).scalars().all()
    today = date.today()
    current_month = f"{today.year:04d}-{today.month:02d}"
    resolved = resolve_year_production_plan(
        headers,
        rows,
        year=target_year,
        merge_month_keys={current_month},
    )
    return headers, resolved.rows, resolved


async def load_latest_production_schedule_from_db(
    db: AsyncSession,
) -> tuple[list[str], list["ScheduleProductPlan"]]:
    """Актуальный помесячный план производства за текущий год из БД."""
    from datetime import date

    from app.agents.document_analysis_agent.excel_service import (
        ScheduleProductPlan,
        _empty_month_bucket,
        _normalize,
    )

    headers, rows, resolved = await _load_resolved_production_plan_rows(db, year=date.today().year)
    if not headers or not rows:
        return [], []
    plans_by_key: dict[str, ScheduleProductPlan] = {}
    for row in rows:
        product = (row.nomenclature_name or "").strip()
        month = (row.month_key or "").strip()
        if not product or not month:
            continue
        key = _normalize(product)
        if not key:
            continue
        plan = plans_by_key.get(key)
        if plan is None:
            spec_key, spec_name = _plan_item_specification(row)
            spec_key = _valid_spec_ref_key(spec_key)
            plan = ScheduleProductPlan(
                product=product,
                monthly_qty={},
                spec_ref_key=spec_key,
                spec_name=spec_name,
            )
            plans_by_key[key] = plan
        else:
            spec_key, spec_name = _plan_item_specification(row)
            spec_key = _valid_spec_ref_key(spec_key)
            new_priority = _plan_spec_priority(spec_key, spec_name)
            old_priority = _plan_spec_priority(plan.spec_ref_key, plan.spec_name)
            if new_priority > old_priority:
                plan.spec_ref_key = spec_key
                plan.spec_name = spec_name
            elif spec_name and not plan.spec_name:
                plan.spec_name = spec_name
            elif spec_key and not plan.spec_ref_key:
                plan.spec_ref_key = spec_key
        bucket = plan.monthly_qty.setdefault(month, _empty_month_bucket())
        bucket["заказ"]["план"] = float(bucket["заказ"].get("план", 0.0)) + float(row.qty or 0.0)

    plans = list(plans_by_key.values())
    source = (
        f"1С → БД: План производства за {resolved.year} год "
        f"({len(resolved.month_sources)} мес., документов: {len(headers)})"
    )
    logger.info(
        "document_analysis_agent.db_production_schedule_loaded",
        products=len(plans),
        rows=len(rows),
        source=source,
    )
    return [source], plans


async def load_latest_detailed_production_schedule_from_db(
    db: AsyncSession,
) -> "DetailedScheduleExtract":
    """Актуальный дневной план производства за текущий год из БД."""
    from datetime import date

    from app.agents.document_analysis_agent.excel_service import (
        DetailedScheduleExtract,
        DetailedScheduleProductPlan,
        _month_day_keys,
        _normalize,
    )

    headers, rows, resolved = await _load_resolved_production_plan_rows(db, year=date.today().year)
    if not headers or not rows:
        return DetailedScheduleExtract(files=[], plans=[], year=0, month=0)

    dated_rows = [row for row in rows if row.product_date is not None]
    if not dated_rows:
        return DetailedScheduleExtract(files=[], plans=[], year=0, month=0)

    months = sorted({(row.month_key or "").strip() for row in dated_rows if (row.month_key or "").strip()})
    if not months:
        return DetailedScheduleExtract(files=[], plans=[], year=0, month=0)

    today = date.today()
    target = f"{today.year:04d}-{today.month:02d}"
    if target in months:
        month_key = target
    else:
        future = [month for month in months if month >= target]
        month_key = future[0] if future else months[-1]

    try:
        year, month_num = (int(part) for part in month_key.split("-", 1))
    except ValueError:
        return DetailedScheduleExtract(files=[], plans=[], year=0, month=0)

    plans_by_key: dict[str, DetailedScheduleProductPlan] = {}
    for row in dated_rows:
        if (row.month_key or "").strip() != month_key or row.product_date is None:
            continue
        product = (row.nomenclature_name or "").strip()
        if not product:
            continue
        key = _normalize(product)
        if not key:
            continue
        plan = plans_by_key.get(key)
        if plan is None:
            plan = DetailedScheduleProductPlan(product=product, year=year, month=month_num)
            plans_by_key[key] = plan
        day_key = row.product_date.date().isoformat()
        plan.daily_qty[day_key] = float(plan.daily_qty.get(day_key, 0.0)) + float(row.qty or 0.0)

    day_keys = _month_day_keys(year, month_num)
    for plan in plans_by_key.values():
        for day_key in day_keys:
            plan.daily_qty.setdefault(day_key, 0.0)

    plans = list(plans_by_key.values())
    month_source = resolved.month_sources.get(month_key)
    source = (
        f"1С → БД: План производства по дням за {month_key}"
        + (f" (№{month_source.number})" if month_source and month_source.number else "")
    )
    logger.info(
        "document_analysis_agent.db_detailed_production_schedule_loaded",
        products=len(plans),
        rows=sum(1 for row in dated_rows if (row.month_key or "").strip() == month_key),
        month=month_key,
        source=source,
    )
    return DetailedScheduleExtract(
        files=[source],
        plans=plans,
        year=year,
        month=month_num,
        day_keys=day_keys,
    )


async def load_plan_product_spec_links_from_db(
    db: AsyncSession,
) -> list["ProductSpecLink"]:
    """Точные связи изделие → спецификация из строк актуального плана 1С за текущий месяц."""
    from datetime import date

    from app.agents.document_analysis_agent.excel_service import ProductSpecLink

    headers, rows, _resolved = await _load_resolved_production_plan_rows(db, year=date.today().year)
    if not headers or not rows:
        return []

    current_month = f"{date.today().year:04d}-{date.today().month:02d}"
    links: list[ProductSpecLink] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        if (row.month_key or "").strip() != current_month:
            continue
        if float(row.qty or 0.0) <= 0:
            continue
        product = (row.nomenclature_name or "").strip()
        if not product:
            continue
        spec_key, spec_name = _plan_item_specification(row)
        spec_key = _valid_spec_ref_key(spec_key)
        spec_name = (spec_name or "").strip()
        if not spec_key and not spec_name:
            continue
        dedupe_key = (product.casefold(), spec_key.lower(), spec_name.casefold())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        links.append(
            ProductSpecLink(
                schedule_product=product,
                nomenclature=product,
                spec_sheet=spec_name or spec_key,
                spec_ref_key=spec_key or None,
                status="matched",
                reason="точная спецификация из строки плана производства 1С",
            )
        )
    logger.info(
        "document_analysis_agent.db_plan_product_spec_links_loaded",
        links=len(links),
        month=current_month,
    )
    return links


def match_product_to_db_spec(
    schedule_product: str,
    nomenclature: str,
    catalog: list[DbSpecCatalogEntry],
    *,
    spec_hint: str = "",
) -> tuple[DbSpecCatalogEntry | None, str]:
    """Подбор ресурсной спецификации из БД (аналог match к листу xlsx)."""
    from app.agents.document_analysis_agent.excel_service import (
        _best_text_match,
        _match_key,
        _match_nomenclature_to_sheet,
        _product_match_score,
    )

    if not catalog:
        return None, "нет спецификаций в БД"

    labels = [entry.label for entry in catalog]
    by_label = {entry.label: entry for entry in catalog}
    spec_hint = (spec_hint or "").strip()
    primary = spec_hint or nomenclature or schedule_product

    if spec_hint:
        hint_key = _match_key(spec_hint)
        for entry in catalog:
            for target in (entry.description, entry.label, entry.main_product_name, entry.code):
                if not target:
                    continue
                target_key = _match_key(target)
                if hint_key and target_key == hint_key:
                    return entry, "БД: спецификация плана (точное совпадение)"
                if hint_key and target_key and (hint_key in target_key or target_key in hint_key):
                    score = _product_match_score(spec_hint, target)
                    if score >= 0.72:
                        return entry, f"БД: спецификация плана ({score:.2f})"

        sheet, reason = _match_nomenclature_to_sheet(schedule_product, spec_hint, labels)
        if sheet and sheet in by_label:
            return by_label[sheet], f"БД: спецификация плана ({reason})"
        best_label, score = _best_text_match(spec_hint, labels)
        if best_label and score >= 0.55 and best_label in by_label:
            return by_label[best_label], f"БД: спецификация плана fuzzy ({score:.2f})"

    sheet, reason = _match_nomenclature_to_sheet(schedule_product, nomenclature, labels)
    if sheet and sheet in by_label:
        return by_label[sheet], f"БД: {reason}"

    # Дополнительно: прямое сопоставление с main_product / description / code
    scored: list[tuple[DbSpecCatalogEntry, float, str]] = []
    for entry in catalog:
        targets = [entry.description, entry.label, entry.main_product_name, entry.code]
        best_target = ""
        best_score = 0.0
        for target in targets:
            if not target:
                continue
            score = max(
                _product_match_score(primary, target),
                _product_match_score(nomenclature, target),
                _product_match_score(schedule_product, target) * 0.92,
            )
            if score > best_score:
                best_score = score
                best_target = target
        if best_score >= 0.45:
            scored.append((entry, best_score, best_target))

    scored.sort(key=lambda item: item[1], reverse=True)
    if scored:
        if len(scored) > 1 and scored[0][1] - scored[1][1] < 0.08 and scored[1][1] >= 0.45:
            return None, "неоднозначный выбор спецификации в БД"
        entry, score, target = scored[0]
        return entry, f"БД: матч по «{target}» ({score:.2f})"

    best_label, score = _best_text_match(primary, labels)
    if best_label and score >= 0.55 and best_label in by_label:
        return by_label[best_label], f"БД: fuzzy ({score:.2f})"

    return None, "спецификация в БД не найдена"


async def load_db_spec_materials(
    db: AsyncSession,
    *,
    ref_key: str,
    product: str,
    spec_label: str,
) -> list["SpecMaterialItem"]:
    from app.agents.document_analysis_agent.excel_service import SpecMaterialItem

    materials = (
        await db.execute(
            select(OnecResourceSpecMaterial)
            .where(OnecResourceSpecMaterial.spec_ref_key == ref_key)
            .order_by(OnecResourceSpecMaterial.line_number)
        )
    ).scalars().all()
    return [
        SpecMaterialItem(
            nomenclature=(row.nomenclature_name or "").strip(),
            quantity=float(row.qty) if row.qty is not None else None,
            product=product,
            unit=(row.unit or "").strip() or None,
            spec_sheet=spec_label,
        )
        for row in materials
        if (row.nomenclature_name or "").strip()
    ]


async def preload_spec_materials_for_links(
    db: AsyncSession,
    links: list["ProductSpecLink"],
) -> dict[str, list["SpecMaterialItem"]]:
    from app.agents.document_analysis_agent.excel_service import ProductSpecLink

    result: dict[str, list] = {}
    for link in links:
        if link.status != "matched":
            continue
        ref_key = _valid_spec_ref_key(link.spec_ref_key or "")
        if not ref_key:
            continue
        lookup_keys = {ref_key, ref_key.lower()}
        if lookup_keys & result.keys():
            continue
        label = link.spec_sheet or link.nomenclature or ref_key
        items = await load_db_spec_materials(
            db,
            ref_key=ref_key,
            product=link.schedule_product,
            spec_label=label,
        )
        result[ref_key] = items
        if ref_key.lower() != ref_key:
            result[ref_key.lower()] = items
        logger.info(
            "document_analysis_agent.db_spec_materials_loaded",
            ref_key=ref_key,
            product=link.schedule_product,
            materials=len(items),
        )
    return result


async def repair_spec_links_without_materials(
    db: AsyncSession,
    links: list["ProductSpecLink"],
    db_materials_by_ref: dict[str, list["SpecMaterialItem"]],
    catalog: list[DbSpecCatalogEntry],
    *,
    product_spec_hints: dict[str, tuple[str, str]] | None = None,
) -> dict[str, list["SpecMaterialItem"]]:
    """Переподбирает спецификацию, если по Ref_Key из плана материалы не загрузились."""
    from app.agents.document_analysis_agent.excel_service import ProductSpecLink, SpecMaterialItem

    updated = dict(db_materials_by_ref)

    def _materials_for_ref(ref_key: str) -> list[SpecMaterialItem]:
        cleaned = _valid_spec_ref_key(ref_key)
        if not cleaned:
            return []
        return updated.get(cleaned) or updated.get(cleaned.lower()) or []

    for link in links:
        if link.status != "matched":
            continue
        if _materials_for_ref(link.spec_ref_key or ""):
            continue

        spec_hint, spec_ref_key = lookup_product_spec_hint(link.schedule_product, product_spec_hints)
        spec_hint = (spec_hint or link.spec_sheet or link.nomenclature or "").strip()
        spec_ref_key = _valid_spec_ref_key(spec_ref_key or link.spec_ref_key or "")

        if spec_ref_key and not _materials_for_ref(spec_ref_key):
            label = spec_hint or link.spec_sheet or link.schedule_product
            items = await load_db_spec_materials(
                db,
                ref_key=spec_ref_key,
                product=link.schedule_product,
                spec_label=label,
            )
            if items:
                updated[spec_ref_key] = items
                updated[spec_ref_key.lower()] = items
                link.spec_ref_key = spec_ref_key
                if spec_hint:
                    link.spec_sheet = spec_hint
                link.reason = f"{link.reason}; материалы по Ref_Key плана".strip("; ")
                continue

            try:
                from app.services.onec_resource_spec_sync import (
                    fetch_resource_spec_materials_by_ref_keys,
                )

                fetched = await asyncio.to_thread(
                    fetch_resource_spec_materials_by_ref_keys,
                    [spec_ref_key],
                )
            except Exception as exc:
                logger.warning(
                    "document_analysis_agent.onec_spec_materials_ref_fetch_failed",
                    product=link.schedule_product,
                    ref_key=spec_ref_key,
                    error=str(exc),
                )
                fetched = {}
            payload = fetched.get(spec_ref_key) or fetched.get(spec_ref_key.lower()) or {}
            fetched_items = [
                SpecMaterialItem(
                    nomenclature=(row.get("nomenclature_name") or "").strip(),
                    quantity=float(row.get("qty") or 0.0),
                    product=link.schedule_product,
                    unit=(row.get("unit") or "").strip() or None,
                    spec_sheet=payload.get("description") or label,
                )
                for row in payload.get("materials") or []
                if (row.get("nomenclature_name") or "").strip()
            ]
            if fetched_items:
                updated[spec_ref_key] = fetched_items
                updated[spec_ref_key.lower()] = fetched_items
                link.spec_ref_key = spec_ref_key
                link.spec_sheet = payload.get("description") or label
                link.reason = f"{link.reason}; материалы точечно загружены из 1С по Ref_Key".strip("; ")
                continue

        if spec_hint:
            entry, reason = match_product_to_db_spec(
                link.schedule_product,
                spec_hint,
                catalog,
                spec_hint=spec_hint,
            )
            if entry is not None and not _materials_for_ref(entry.ref_key):
                label = entry.description or entry.label
                items = await load_db_spec_materials(
                    db,
                    ref_key=entry.ref_key,
                    product=link.schedule_product,
                    spec_label=label,
                )
                if items:
                    link.spec_ref_key = entry.ref_key
                    link.spec_sheet = label
                    link.nomenclature = entry.main_product_name or spec_hint
                    link.reason = f"{link.reason}; {reason}".strip("; ")
                    updated[entry.ref_key] = items
                    updated[entry.ref_key.lower()] = items
                    continue

        logger.warning(
            "document_analysis_agent.db_spec_materials_missing",
            product=link.schedule_product,
            ref_key=link.spec_ref_key,
            spec_hint=spec_hint,
        )

    return updated


def _materials_for_ref_key(
    db_materials_by_ref: dict[str, list] | None,
    ref_key: str,
) -> list:
    if not db_materials_by_ref:
        return []
    cleaned = _valid_spec_ref_key(ref_key)
    if not cleaned:
        return []
    return db_materials_by_ref.get(cleaned) or db_materials_by_ref.get(cleaned.lower()) or []


def _catalog_ref_keys(catalog: list[DbSpecCatalogEntry] | None) -> set[str]:
    keys: set[str] = set()
    if not catalog:
        return keys
    for entry in catalog:
        ref = (entry.ref_key or "").strip()
        if not ref:
            continue
        keys.add(ref)
        keys.add(ref.lower())
    return keys


def link_has_loaded_onec_spec(
    link: "ProductSpecLink",
    db_materials_by_ref: dict[str, list] | None,
    catalog: list[DbSpecCatalogEntry] | None = None,
) -> bool:
    """True, если у изделия есть спецификация 1С в каталоге БД и загружены материалы."""
    from app.agents.document_analysis_agent.excel_service import ProductSpecLink

    if not isinstance(link, ProductSpecLink):
        return False
    if link.status != "matched":
        return False
    ref_key = _valid_spec_ref_key(link.spec_ref_key or "")
    if not ref_key:
        return False
    catalog_keys = _catalog_ref_keys(catalog)
    if catalog_keys and ref_key not in catalog_keys and ref_key.lower() not in catalog_keys:
        return False
    return bool(_materials_for_ref_key(db_materials_by_ref, ref_key))


def products_with_loaded_onec_specs(
    links: list["ProductSpecLink"],
    db_materials_by_ref: dict[str, list] | None,
    catalog: list[DbSpecCatalogEntry] | None = None,
) -> frozenset[str]:
    """Имена изделий графика с валидной ресурсной спецификацией 1С (каталог + материалы)."""
    result: set[str] = set()
    for link in links:
        if not link_has_loaded_onec_spec(link, db_materials_by_ref, catalog):
            continue
        product = (link.schedule_product or "").strip()
        if product:
            result.add(product)
    return frozenset(result)


def expand_spec_eligible_product_names(
    eligible: frozenset[str],
    schedule_plans: list[Any],
    detailed_plans: list[Any],
) -> frozenset[str]:
    """Сопоставляет имена из графиков с изделиями, у которых есть спека 1С."""
    from app.agents.document_analysis_agent.product_coverage import _normalize

    expanded = set(eligible)
    eligible_by_norm = {_normalize(name): name for name in eligible}
    for plan in list(schedule_plans) + list(detailed_plans):
        name = (getattr(plan, "product", "") or "").strip()
        if not name or name in expanded:
            continue
        if _normalize(name) in eligible_by_norm:
            expanded.add(name)
    return frozenset(expanded)


def finalize_onec_spec_links(
    links: list["ProductSpecLink"],
    db_materials_by_ref: dict[str, list] | None,
    catalog: list[DbSpecCatalogEntry] | None = None,
) -> int:
    """Понижает matched-ссылки без каталога/материалов 1С — не участвуют в расчёте."""
    from app.agents.document_analysis_agent.excel_service import ProductSpecLink

    demoted = 0
    catalog_keys = _catalog_ref_keys(catalog)
    for link in links:
        if not isinstance(link, ProductSpecLink):
            continue
        if link.status != "matched":
            continue
        ref_key = _valid_spec_ref_key(link.spec_ref_key or "")
        if not ref_key:
            link.status = "unmatched"
            link.reason = f"{link.reason}; нет Ref_Key спецификации 1С".strip("; ")
            demoted += 1
            continue
        if catalog_keys and ref_key not in catalog_keys and ref_key.lower() not in catalog_keys:
            link.status = "unmatched"
            link.reason = f"{link.reason}; спецификация отсутствует в каталоге 1С".strip("; ")
            demoted += 1
            continue
        if not _materials_for_ref_key(db_materials_by_ref, ref_key):
            link.status = "unmatched"
            link.reason = f"{link.reason}; материалы спецификации 1С не загружены".strip("; ")
            demoted += 1
    if demoted:
        logger.info(
            "document_analysis_agent.onec_spec_links_finalized",
            demoted=demoted,
            total=len(links),
        )
    return demoted
