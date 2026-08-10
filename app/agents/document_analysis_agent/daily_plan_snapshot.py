"""Снимки дневного листа «2-произв. план (Месяц)» для повторного использования в result.xlsx."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.logging import get_logger

logger = get_logger(__name__)

_SNAPSHOT_ROOT = (
    Path(__file__).resolve().parents[3] / "data" / "aveon" / "daily_plan_snapshots"
)
_SAFE_SCOPE_RE = re.compile(r"[^a-zA-Z0-9_\-]+")
_PERIOD_KEY_RE = re.compile(r"^\d{4}-\d{2}$")


def _scope_key(user_id: UUID | str | None) -> str:
    if user_id is None:
        return "anonymous"
    raw = str(user_id).strip()
    cleaned = _SAFE_SCOPE_RE.sub("_", raw).strip("._") or "anonymous"
    return cleaned[:120]


def _user_dir(user_id: UUID | str | None) -> Path:
    return _SNAPSHOT_ROOT / _scope_key(user_id)


def period_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def list_daily_plan_snapshots(
    user_id: UUID | str | None,
    *,
    exclude_period: str | None = None,
    before_period: str | None = None,
) -> list[dict[str, Any]]:
    """Снимки дневных листов из `data/aveon/daily_plan_snapshots` (корень и подпапки)."""
    by_period: dict[str, dict[str, Any]] = {}

    for path in sorted(_SNAPSHOT_ROOT.rglob("*.json")):
        if not _PERIOD_KEY_RE.match(path.stem):
            continue
        loaded = _load_snapshot_file(path)
        if loaded is not None:
            by_period[str(loaded.get("period_key") or path.stem)] = loaded

    user_directory = _user_dir(user_id)
    if user_directory.is_dir():
        for path in sorted(user_directory.glob("*.json")):
            if not _PERIOD_KEY_RE.match(path.stem):
                continue
            loaded = _load_snapshot_file(path)
            if loaded is not None:
                by_period[str(loaded.get("period_key") or path.stem)] = loaded

    snapshots = list(by_period.values())
    if exclude_period:
        snapshots = [
            item
            for item in snapshots
            if str(item.get("period_key") or "") != exclude_period
        ]
    if before_period:
        snapshots = [
            item
            for item in snapshots
            if str(item.get("period_key") or "") < before_period
        ]

    snapshots.sort(key=lambda item: str(item.get("period_key") or ""))
    return snapshots


def load_daily_plan_snapshot(
    user_id: UUID | str | None,
    *,
    year: int,
    month: int,
) -> dict[str, Any] | None:
    path = _user_dir(user_id) / f"{period_key(year, month)}.json"
    return _load_snapshot_file(path)


def save_daily_plan_snapshot(
    user_id: UUID | str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Сохраняет/перезаписывает снимок дневного листа за месяц."""
    year = int(payload.get("year") or 0)
    month = int(payload.get("month") or 0)
    if year <= 0 or month <= 0:
        raise ValueError("daily_plan_snapshot: year/month required")

    directory = _user_dir(user_id)
    directory.mkdir(parents=True, exist_ok=True)

    body = {
        "version": 1,
        "user_id": str(user_id) if user_id is not None else None,
        "year": year,
        "month": month,
        "period_key": period_key(year, month),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    path = directory / f"{body['period_key']}.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "document_analysis_agent.daily_plan_snapshot_saved",
        path=str(path),
        period=body["period_key"],
        rows=len(body.get("rows") or []),
        days=len(body.get("day_keys") or []),
    )
    return body


def _load_snapshot_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "document_analysis_agent.daily_plan_snapshot_load_failed",
            path=str(path),
            error=str(exc),
        )
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("period_key"):
        year = int(data.get("year") or 0)
        month = int(data.get("month") or 0)
        if year > 0 and month > 0:
            data["period_key"] = period_key(year, month)
    return data
