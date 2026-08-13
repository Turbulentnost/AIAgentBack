"""Сопоставление остатков с номенклатурой из ресурсных спецификаций 1С."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onec_resource_spec import OnecResourceSpecMaterial
from app.services.onec_resource_spec_sync import ensure_onec_resource_spec_tables

EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


def normalize_nomenclature_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().replace("ё", "е")
    return text.casefold()


@dataclass(frozen=True)
class SpecNomenclatureIndex:
    keys: frozenset[str]
    codes: frozenset[str]
    names: frozenset[str]
    materials_count: int

    @property
    def size(self) -> int:
        return self.materials_count

    def matches(
        self,
        *,
        nomenclature_key: str | None = None,
        code: str | None = None,
        name: str | None = None,
    ) -> bool:
        key = (nomenclature_key or "").strip()
        if key and key != EMPTY_GUID and key in self.keys:
            return True
        normalized_code = normalize_nomenclature_text(code or "")
        if normalized_code and normalized_code in self.codes:
            return True
        normalized_name = normalize_nomenclature_text(name or "")
        if normalized_name and normalized_name in self.names:
            return True
        return False


async def load_spec_nomenclature_index(db: AsyncSession) -> SpecNomenclatureIndex:
    """Уникальная номенклатура из материалов всех ресурсных спецификаций."""
    await ensure_onec_resource_spec_tables()
    rows = (
        await db.execute(
            select(
                OnecResourceSpecMaterial.nomenclature_key,
                OnecResourceSpecMaterial.nomenclature_code,
                OnecResourceSpecMaterial.nomenclature_name,
            )
        )
    ).all()

    keys: set[str] = set()
    codes: set[str] = set()
    names: set[str] = set()
    seen_materials: set[str] = set()
    materials_count = 0
    for nomenclature_key, nomenclature_code, nomenclature_name in rows:
        key = (nomenclature_key or "").strip()
        code = normalize_nomenclature_text(nomenclature_code or "")
        name = normalize_nomenclature_text(nomenclature_name or "")
        identity = key if key and key != EMPTY_GUID else code or name
        if identity and identity not in seen_materials:
            seen_materials.add(identity)
            materials_count += 1
        if key and key != EMPTY_GUID:
            keys.add(key)
        if code:
            codes.add(code)
        if name:
            names.add(name)

    return SpecNomenclatureIndex(
        keys=frozenset(keys),
        codes=frozenset(codes),
        names=frozenset(names),
        materials_count=materials_count,
    )


def stock_row_matches_spec(row: object, index: SpecNomenclatureIndex) -> bool:
    return index.matches(
        nomenclature_key=getattr(row, "nomenclature_key", None),
        code=getattr(row, "code", None),
        name=getattr(row, "name", None),
    )
