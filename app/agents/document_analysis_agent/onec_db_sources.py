"""Остатки и ресурсные спецификации из PostgreSQL (синхронизация 1С) для агента Aveon."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.onec_resource_spec import OnecResourceSpec, OnecResourceSpecMaterial
from app.models.onec_nomenclature import OnecNomenclature
from app.models.onec_stock import OnecStockBalance
from app.services.onec_resource_spec_sync import ensure_onec_resource_spec_tables
from app.services.onec_stock_sync import ensure_onec_stock_tables

logger = get_logger(__name__)


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

    await ensure_onec_stock_tables(db)
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


async def build_country_index_from_db(db: AsyncSession) -> dict[str, DbNomenclatureCountryEntry]:
    """Индекс страна происхождения по нормализованному имени номенклатуры."""
    from app.agents.document_analysis_agent.excel_service import _normalize

    await ensure_onec_resource_spec_tables()
    rows = (await db.execute(select(OnecNomenclature))).scalars().all()
    index: dict[str, DbNomenclatureCountryEntry] = {}
    for row in rows:
        name = (row.name or "").strip()
        if not name:
            continue
        key = _normalize(name)
        if not key:
            continue
        index[key] = DbNomenclatureCountryEntry(
            nomenclature=name,
            country_of_origin=(row.country_of_origin or "").strip(),
            code=(row.code or "").strip(),
            ref_key=row.ref_key,
        )
    logger.info(
        "document_analysis_agent.db_country_loaded",
        unique=len(index),
        with_country=sum(1 for item in index.values() if item.country_of_origin),
    )
    return index


async def build_unit_index_from_db(db: AsyncSession) -> dict[str, DbNomenclatureUnitEntry]:
    """Индекс единицы измерения по нормализованному имени номенклатуры."""
    from app.agents.document_analysis_agent.excel_service import _normalize

    await ensure_onec_resource_spec_tables()
    rows = (await db.execute(select(OnecNomenclature))).scalars().all()
    index: dict[str, DbNomenclatureUnitEntry] = {}
    for row in rows:
        name = (row.name or "").strip()
        if not name:
            continue
        key = _normalize(name)
        if not key:
            continue
        index[key] = DbNomenclatureUnitEntry(
            nomenclature=name,
            unit=(row.unit or "").strip(),
            code=(row.code or "").strip(),
            ref_key=row.ref_key,
        )
    logger.info(
        "document_analysis_agent.db_unit_loaded",
        unique=len(index),
        with_unit=sum(1 for item in index.values() if item.unit),
    )
    return index


async def load_db_spec_catalog(db: AsyncSession) -> list[DbSpecCatalogEntry]:
    await ensure_onec_resource_spec_tables()
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


def match_product_to_db_spec(
    schedule_product: str,
    nomenclature: str,
    catalog: list[DbSpecCatalogEntry],
) -> tuple[DbSpecCatalogEntry | None, str]:
    """Подбор ресурсной спецификации из БД (аналог match к листу xlsx)."""
    from app.agents.document_analysis_agent.excel_service import (
        _best_text_match,
        _match_nomenclature_to_sheet,
        _product_match_score,
    )

    if not catalog:
        return None, "нет спецификаций в БД"

    labels = [entry.label for entry in catalog]
    by_label = {entry.label: entry for entry in catalog}

    sheet, reason = _match_nomenclature_to_sheet(schedule_product, nomenclature, labels)
    if sheet and sheet in by_label:
        return by_label[sheet], f"БД: {reason}"

    # Дополнительно: прямое сопоставление с main_product / description / code
    scored: list[tuple[DbSpecCatalogEntry, float, str]] = []
    for entry in catalog:
        targets = [entry.main_product_name, entry.description, entry.label, entry.code]
        best_target = ""
        best_score = 0.0
        for target in targets:
            if not target:
                continue
            score = max(
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

    best_label, score = _best_text_match(nomenclature, labels)
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
        if link.status != "matched" or not link.spec_ref_key:
            continue
        if link.spec_ref_key in result:
            continue
        label = link.spec_sheet or link.nomenclature or link.spec_ref_key
        items = await load_db_spec_materials(
            db,
            ref_key=link.spec_ref_key,
            product=link.schedule_product,
            spec_label=label,
        )
        result[link.spec_ref_key] = items
        logger.info(
            "document_analysis_agent.db_spec_materials_loaded",
            ref_key=link.spec_ref_key,
            product=link.schedule_product,
            materials=len(items),
        )
    return result
