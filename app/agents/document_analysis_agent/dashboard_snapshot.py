"""Персистентный снимок дашборда агента Авион (контрольные точки / сводка).

Хранится на диске: data/aveon/dashboard_snapshots/<scope>.json
Переживает перезагрузку страницы и перезапуск backend; обновляется после каждого анализа.

v2: дашборд по заданиям и прогресс менеджера — до следующего анализа;
сменное задание (файл + модалка) — до конца календарного дня (00:00 МСК).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.logging import get_logger

logger = get_logger(__name__)

_MSK = ZoneInfo("Europe/Moscow")
_SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "data" / "aveon" / "dashboard_snapshots"
_SAFE_SCOPE_RE = re.compile(r"[^a-zA-Z0-9_\-]+")


def today_msk_iso() -> str:
    return datetime.now(_MSK).date().isoformat()


def _scope_key(user_id: UUID | str | None) -> str:
    if user_id is None:
        return "anonymous"
    raw = str(user_id).strip()
    cleaned = _SAFE_SCOPE_RE.sub("_", raw).strip("._") or "anonymous"
    return cleaned[:120]


def _snapshot_path(user_id: UUID | str | None) -> Path:
    return _SNAPSHOT_DIR / f"{_scope_key(user_id)}.json"


def is_shift_assignment_valid(snapshot: dict[str, Any]) -> bool:
    """Сменное задание доступно до 00:00 МСК следующего дня после valid_date."""
    shift = snapshot.get("shift_assignment")
    if not isinstance(shift, dict):
        return False
    valid_date = shift.get("valid_date")
    if not valid_date:
        return False
    return str(valid_date) >= today_msk_iso()


def _sanitize_result_evals(result_evals: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result_evals, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in result_evals.items():
        if not isinstance(value, dict):
            continue
        cleaned[str(key)] = {
            k: v
            for k, v in value.items()
            if k in {"status", "comment", "error"} and v is not None
        }
    return cleaned


def save_dashboard_snapshot(
    user_id: UUID | str | None,
    *,
    logistics_risks: dict[str, Any],
    analyzed_at: str,
    task_dashboard: dict[str, Any] | None = None,
    shift_assignment: dict[str, Any] | None = None,
    merged_shipment_schedule: dict[str, Any] | None = None,
    coverage_dashboard: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Сохраняет/перезаписывает последний снимок дашборда для пользователя."""
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "version": 2,
        "user_id": str(user_id) if user_id is not None else None,
        "analyzed_at": analyzed_at,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "logistics_risks": logistics_risks,
        "task_dashboard": task_dashboard,
        "shift_assignment": shift_assignment,
        "merged_shipment_schedule": merged_shipment_schedule,
        "coverage_dashboard": coverage_dashboard,
        "meta": meta or {},
    }
    path = _snapshot_path(user_id)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "document_analysis_agent.dashboard_snapshot_saved",
        path=str(path),
        stages=len((logistics_risks or {}).get("stages") or []),
        has_task_dashboard=bool(task_dashboard),
        has_shift=bool(shift_assignment),
        has_merged_shipment=bool(merged_shipment_schedule),
        has_coverage=bool(coverage_dashboard),
    )
    return payload


def update_merged_shipment_snapshot(
    user_id: UUID | str | None,
    *,
    merged_shipment_schedule: dict[str, Any],
) -> dict[str, Any] | None:
    """Сохраняет объединённый график отгрузок без повторного анализа."""
    path = _snapshot_path(user_id)
    if not path.is_file():
        data: dict[str, Any] = {
            "version": 2,
            "user_id": str(user_id) if user_id is not None else None,
            "analyzed_at": datetime.now(_MSK).isoformat(timespec="seconds"),
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "logistics_risks": {"as_of": None, "stages": []},
            "task_dashboard": None,
            "shift_assignment": None,
            "merged_shipment_schedule": merged_shipment_schedule,
            "meta": {"source": "shipment_merge"},
        }
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        data["merged_shipment_schedule"] = merged_shipment_schedule
    data["shipment_saved_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def update_task_progress(
    user_id: UUID | str | None,
    *,
    result_texts: dict[str, str] | None = None,
    result_evals: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Обновляет прогресс менеджера в снимке без повторного анализа."""
    path = _snapshot_path(user_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    task_dashboard = data.get("task_dashboard")
    if not isinstance(task_dashboard, dict):
        return None
    if result_texts is not None:
        task_dashboard["result_texts"] = {
            str(k): str(v) for k, v in result_texts.items() if v is not None
        }
    if result_evals is not None:
        task_dashboard["result_evals"] = _sanitize_result_evals(result_evals)
    data["task_dashboard"] = task_dashboard
    data["progress_saved_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "document_analysis_agent.task_progress_saved",
        path=str(path),
        texts=len(task_dashboard.get("result_texts") or {}),
        evals=len(task_dashboard.get("result_evals") or {}),
    )
    return data


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
    if not is_shift_assignment_valid(data):
        data["shift_assignment"] = None
    return data


def clear_dashboard_snapshot(user_id: UUID | str | None) -> bool:
    path = _snapshot_path(user_id)
    if not path.is_file():
        return False
    path.unlink(missing_ok=True)
    return True
