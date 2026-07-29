"""Персистентный снимок дашборда агента Авион (контрольные точки / сводка).

Хранится на диске: data/aveon/dashboard_snapshots/<scope>.json
Переживает перезагрузку страницы и перезапуск backend; обновляется после каждого анализа.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.logging import get_logger

logger = get_logger(__name__)

_SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "data" / "aveon" / "dashboard_snapshots"
_SAFE_SCOPE_RE = re.compile(r"[^a-zA-Z0-9_\-]+")


def _scope_key(user_id: UUID | str | None) -> str:
    if user_id is None:
        return "anonymous"
    raw = str(user_id).strip()
    cleaned = _SAFE_SCOPE_RE.sub("_", raw).strip("._") or "anonymous"
    return cleaned[:120]


def _snapshot_path(user_id: UUID | str | None) -> Path:
    return _SNAPSHOT_DIR / f"{_scope_key(user_id)}.json"


def save_dashboard_snapshot(
    user_id: UUID | str | None,
    *,
    logistics_risks: dict[str, Any],
    analyzed_at: str,
    shift_assignment_file_name: str | None = None,
    shift_assignment_file_base64: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Сохраняет/перезаписывает последний снимок дашборда для пользователя."""
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "version": 1,
        "user_id": str(user_id) if user_id is not None else None,
        "analyzed_at": analyzed_at,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "logistics_risks": logistics_risks,
        "shift_assignment_file_name": shift_assignment_file_name,
        "shift_assignment_file_base64": shift_assignment_file_base64,
        "meta": meta or {},
    }
    path = _snapshot_path(user_id)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "document_analysis_agent.dashboard_snapshot_saved",
        path=str(path),
        stages=len((logistics_risks or {}).get("stages") or []),
        has_shift=bool(shift_assignment_file_base64),
    )
    return payload


def load_dashboard_snapshot(user_id: UUID | str | None) -> dict[str, Any] | None:
    """Читает последний снимок; None если файла нет или он битый."""
    path = _snapshot_path(user_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "document_analysis_agent.dashboard_snapshot_load_failed",
            path=str(path),
            error=str(exc),
        )
        return None
    if not isinstance(data, dict):
        return None
    risks = data.get("logistics_risks")
    if not isinstance(risks, dict):
        return None
    return data


def clear_dashboard_snapshot(user_id: UUID | str | None) -> bool:
    path = _snapshot_path(user_id)
    if not path.is_file():
        return False
    path.unlink(missing_ok=True)
    return True
