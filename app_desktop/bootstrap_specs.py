"""Загрузка встроенного снимка спецификаций в SQLite (без PostgreSQL / 1С)."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.onec_resource_spec import OnecResourceSpec
from app.services.onec_resource_spec_sync import (
    ensure_onec_resource_spec_tables,
    replace_resource_specs_in_db,
)

logger = get_logger(__name__)


def desktop_specs_snapshot_path() -> Path:
    """JSON со спецификациями: рядом с кодом или в PyInstaller bundle."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "app_desktop" / "data" / "resource_specs_snapshot.json")
    candidates.append(Path(__file__).resolve().parent / "data" / "resource_specs_snapshot.json")
    for path in candidates:
        if path.is_file():
            return path
    return candidates[-1]


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _normalize_specs(raw_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for item in raw_specs:
        row = dict(item)
        row["valid_from"] = _parse_dt(row.get("valid_from"))
        row["valid_to"] = _parse_dt(row.get("valid_to"))
        materials = list(row.get("materials") or [])
        outputs = list(row.get("outputs") or [])
        row["materials"] = materials
        row["outputs"] = outputs
        row["materials_count"] = int(row.get("materials_count") or len(materials))
        row["outputs_count"] = int(row.get("outputs_count") or len(outputs))
        specs.append(row)
    return specs


async def ensure_desktop_resource_specs() -> dict[str, Any]:
    """Если в SQLite нет спецификаций — заливает встроенный snapshot."""
    await ensure_onec_resource_spec_tables()
    snapshot = desktop_specs_snapshot_path()
    async with AsyncSessionLocal() as db:
        existing = int(
            await db.scalar(select(func.count()).select_from(OnecResourceSpec)) or 0
        )
        if existing > 0:
            logger.info("app_desktop.specs_seed_skipped", db_specs=existing)
            return {"ok": True, "skipped": True, "db_specs": existing}

        if not snapshot.is_file():
            logger.warning("app_desktop.specs_snapshot_missing", path=str(snapshot))
            return {"ok": False, "skipped": False, "error": "snapshot_missing", "path": str(snapshot)}

        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        specs = _normalize_specs(list(payload.get("specs") or []))
        nomenclature = list(payload.get("nomenclature") or [])
        if not specs:
            return {"ok": False, "skipped": False, "error": "snapshot_empty"}

        result = await replace_resource_specs_in_db(db, specs, nomenclature)
        await db.commit()
        logger.info(
            "app_desktop.specs_seeded",
            path=str(snapshot),
            saved_specs=result.get("saved_specs"),
            saved_materials=result.get("saved_materials"),
        )
        return {"ok": True, "skipped": False, **result}


__all__ = [
    "desktop_specs_snapshot_path",
    "ensure_desktop_resource_specs",
]
