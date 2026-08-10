"""Персистентные версии графиков производства для сравнения между анализами.

Хранится на диске:
  data/aveon/schedule_snapshots/<scope>/meta.json
  data/aveon/schedule_snapshots/<scope>/production.xlsx
  data/aveon/schedule_snapshots/<scope>/detailed_YYYY-MM.xlsx  (по одному на месяц)

Помесячный график производства — одна базовая версия на пользователя.
Детальный график — отдельная базовая версия на каждый месяц (YYYY-MM).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.agents.document_analysis_agent.production_schedule_diff import extract_schedule_version
from app.core.logging import get_logger

logger = get_logger(__name__)

_SNAPSHOT_ROOT = Path(__file__).resolve().parents[3] / "data" / "aveon" / "schedule_snapshots"
_SAFE_SCOPE_RE = re.compile(r"[^a-zA-Z0-9_\-]+")

PRODUCTION_FILE = "production.xlsx"
DETAILED_FILE = "detailed.xlsx"


def detailed_month_key(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def detailed_storage_name(year: int, month: int) -> str:
    return f"detailed_{detailed_month_key(year, month)}.xlsx"


def _scope_key(user_id: UUID | str | None) -> str:
    if user_id is None:
        return "anonymous"
    raw = str(user_id).strip()
    cleaned = _SAFE_SCOPE_RE.sub("_", raw).strip("._") or "anonymous"
    return cleaned[:120]


def _user_dir(user_id: UUID | str | None) -> Path:
    return _SNAPSHOT_ROOT / _scope_key(user_id)


def _meta_path(user_id: UUID | str | None) -> Path:
    return _user_dir(user_id) / "meta.json"


def _legacy_meta_path(user_id: UUID | str | None) -> Path:
    return _SNAPSHOT_ROOT / f"{_scope_key(user_id)}.json"


def _read_meta_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "document_analysis_agent.schedule_snapshot_load_failed",
            path=str(path),
            error=str(exc),
        )
        return None
    return data if isinstance(data, dict) else None


def _normalize_detailed_schedules(meta: dict[str, Any]) -> dict[str, Any]:
    raw = meta.get("detailed_schedules")
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}
    return {}


def _migrate_legacy_detailed_entry(
    user_id: UUID | str | None,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Переносит одиночный detailed.xlsx в detailed_schedules по месяцу из файла."""
    schedules = _normalize_detailed_schedules(meta)
    if schedules:
        return schedules

    legacy_entry = meta.get("detailed_schedule")
    if not isinstance(legacy_entry, dict):
        return schedules

    legacy_path = _user_dir(user_id) / DETAILED_FILE
    if not legacy_path.is_file():
        return schedules

    year = int(legacy_entry.get("year") or 0)
    month = int(legacy_entry.get("month") or 0)
    if year <= 0 or month <= 0:
        try:
            from app.agents.document_analysis_agent.detailed_schedule_diff import (
                infer_detailed_workbook_month,
            )

            inferred = infer_detailed_workbook_month(legacy_path.read_bytes())
            if inferred is not None:
                year, month = inferred
        except Exception:
            inferred = None

    if year <= 0 or month <= 0:
        return schedules

    month_key = detailed_month_key(year, month)
    stored_name = detailed_storage_name(year, month)
    target = _user_dir(user_id) / stored_name
    if legacy_path != target:
        try:
            target.write_bytes(legacy_path.read_bytes())
        except OSError as exc:
            logger.warning(
                "document_analysis_agent.schedule_snapshot_migrate_failed",
                path=str(target),
                error=str(exc),
            )
            return schedules

    schedules[month_key] = {
        **legacy_entry,
        "year": year,
        "month": month,
        "file_name": stored_name,
        "stored_path": stored_name,
    }
    return schedules


def load_schedule_snapshot(user_id: UUID | str | None) -> dict[str, Any] | None:
    """Читает метаданные сохранённых графиков; None если снимка нет."""
    data = _read_meta_file(_meta_path(user_id))
    if data is None:
        data = _read_meta_file(_legacy_meta_path(user_id))
    if data is None:
        return None

    detailed_schedules = _migrate_legacy_detailed_entry(user_id, data)
    if detailed_schedules and not _normalize_detailed_schedules(data):
        data = {**data, "version": max(int(data.get("version") or 0), 3), "detailed_schedules": detailed_schedules}
        try:
            _meta_path(user_id).write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
    elif detailed_schedules:
        data["detailed_schedules"] = detailed_schedules
    return data


def _entry_file_path(user_id: UUID | str | None, entry: dict[str, Any] | None) -> Path | None:
    if not isinstance(entry, dict):
        return None
    file_name = entry.get("file_name")
    if isinstance(file_name, str) and file_name:
        candidate = _user_dir(user_id) / file_name
        if candidate.is_file():
            return candidate
    stored_path = entry.get("stored_path")
    if isinstance(stored_path, str) and stored_path:
        candidate = _user_dir(user_id) / stored_path
        if candidate.is_file():
            return candidate
    return None


def get_saved_production_file(user_id: UUID | str | None) -> tuple[str, bytes] | None:
    """Возвращает (filename, bytes) сохранённого помесячного графика."""
    meta = load_schedule_snapshot(user_id)
    if meta is None:
        return None
    entry = meta.get("production_schedule")
    path = _entry_file_path(user_id, entry if isinstance(entry, dict) else None)
    if path is None:
        legacy_path = _user_dir(user_id) / PRODUCTION_FILE
        if legacy_path.is_file():
            path = legacy_path
        else:
            return None
    filename = ""
    if isinstance(entry, dict):
        filename = str(entry.get("filename") or entry.get("files", [""])[0] or path.name)
    if not filename:
        filename = path.name
    try:
        return filename, path.read_bytes()
    except OSError as exc:
        logger.warning(
            "document_analysis_agent.schedule_snapshot_read_failed",
            path=str(path),
            error=str(exc),
        )
        return None


def get_saved_detailed_file(
    user_id: UUID | str | None,
    year: int,
    month: int,
) -> tuple[str, bytes] | None:
    """Возвращает (filename, bytes) сохранённого детального графика за указанный месяц."""
    if year <= 0 or month <= 0:
        return None
    meta = load_schedule_snapshot(user_id)
    if meta is None:
        return None

    month_key = detailed_month_key(year, month)
    schedules = _normalize_detailed_schedules(meta)
    entry = schedules.get(month_key)
    path = _entry_file_path(user_id, entry)
    if path is None:
        fallback = _user_dir(user_id) / detailed_storage_name(year, month)
        if fallback.is_file():
            path = fallback
        else:
            return None

    filename = ""
    if isinstance(entry, dict):
        filename = str(entry.get("filename") or path.name)
    if not filename:
        filename = path.name
    try:
        return filename, path.read_bytes()
    except OSError as exc:
        logger.warning(
            "document_analysis_agent.schedule_snapshot_read_failed",
            path=str(path),
            error=str(exc),
        )
        return None


def list_saved_detailed_schedules(user_id: UUID | str | None) -> list[dict[str, Any]]:
    """Список сохранённых базовых детальных графиков по месяцам."""
    meta = load_schedule_snapshot(user_id) or {}
    schedules = _normalize_detailed_schedules(meta)
    items: list[dict[str, Any]] = []
    for month_key in sorted(schedules.keys()):
        entry = schedules[month_key]
        year = int(entry.get("year") or 0)
        month = int(entry.get("month") or 0)
        if (year <= 0 or month <= 0) and len(month_key) == 7:
            try:
                year = int(month_key[:4])
                month = int(month_key[5:7])
            except ValueError:
                pass
        has_file = get_saved_detailed_file(user_id, year, month) is not None
        items.append(
            {
                "month": month_key,
                "year": year,
                "month_num": month,
                "filename": str(entry.get("filename") or ""),
                "version_label": str(entry.get("version_label") or ""),
                "saved_at": str(entry.get("saved_at") or meta.get("saved_at") or ""),
                "has_file": has_file,
            }
        )
    return items


def save_schedule_snapshot(
    user_id: UUID | str | None,
    *,
    production: tuple[str, bytes] | None = None,
    detailed: tuple[int, int, str, bytes] | None = None,
    analyzed_at: str | None = None,
) -> dict[str, Any]:
    """Сохраняет/обновляет базовые версии графиков после успешного анализа."""
    user_dir = _user_dir(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    existing = load_schedule_snapshot(user_id) or {}
    payload: dict[str, Any] = {
        "version": 3,
        "user_id": str(user_id) if user_id is not None else None,
        "analyzed_at": analyzed_at or datetime.now(timezone.utc).isoformat(),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    if production is not None:
        filename, content = production
        target = user_dir / PRODUCTION_FILE
        target.write_bytes(content)
        _version, version_label = extract_schedule_version(content)
        payload["production_schedule"] = {
            "filename": filename,
            "file_name": PRODUCTION_FILE,
            "stored_path": PRODUCTION_FILE,
            "version": _version,
            "version_label": version_label,
            "saved_at": payload["saved_at"],
        }
    elif isinstance(existing.get("production_schedule"), dict):
        payload["production_schedule"] = existing["production_schedule"]

    detailed_schedules = _normalize_detailed_schedules(existing)
    if detailed is not None:
        year, month, filename, content = detailed
        month_key = detailed_month_key(year, month)
        stored_name = detailed_storage_name(year, month)
        target = user_dir / stored_name
        target.write_bytes(content)
        _version, version_label = extract_schedule_version(content)
        detailed_schedules[month_key] = {
            "filename": filename,
            "file_name": stored_name,
            "stored_path": stored_name,
            "year": int(year),
            "month": int(month),
            "version": _version,
            "version_label": version_label,
            "saved_at": payload["saved_at"],
        }
    payload["detailed_schedules"] = detailed_schedules

    meta_path = _meta_path(user_id)
    meta_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    legacy_path = _legacy_meta_path(user_id)
    if legacy_path.is_file() and legacy_path != meta_path:
        try:
            legacy_path.unlink(missing_ok=True)
        except OSError:
            pass

    logger.info(
        "document_analysis_agent.schedule_snapshot_saved",
        path=str(meta_path),
        has_production=bool(payload.get("production_schedule")),
        detailed_months=sorted(detailed_schedules.keys()),
    )
    return payload


def schedule_snapshot_status(user_id: UUID | str | None) -> dict[str, Any]:
    """Краткий статус сохранённых базовых версий для UI."""
    meta = load_schedule_snapshot(user_id) or {}
    production = meta.get("production_schedule") if isinstance(meta.get("production_schedule"), dict) else {}
    detailed_items = list_saved_detailed_schedules(user_id)
    latest_detailed = detailed_items[-1] if detailed_items else {}
    return {
        "has_production": bool(get_saved_production_file(user_id)),
        "has_detailed": any(item.get("has_file") for item in detailed_items),
        "production_version": str(production.get("version_label") or ""),
        "production_filename": str(production.get("filename") or ""),
        "production_saved_at": str(production.get("saved_at") or meta.get("saved_at") or ""),
        "detailed_version": str(latest_detailed.get("version_label") or ""),
        "detailed_filename": str(latest_detailed.get("filename") or ""),
        "detailed_saved_at": str(latest_detailed.get("saved_at") or ""),
        "detailed_schedules": detailed_items,
    }
