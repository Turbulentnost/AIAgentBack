"""Персистентный снимок дашборда агента Авион (контрольные точки / сводка).

Хранится на диске: data/aveon/dashboard_snapshots/<scope>.json
Переживает перезагрузку страницы и перезапуск backend; обновляется после каждого анализа.

v2: дашборд по заданиям, прогресс менеджера и сменное задание — до конца календарного дня
(valid_date, 00:00 МСК следующего дня). На новый день нужен актуальный анализ Excel.
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


def derive_dashboard_date_msk(snapshot: dict[str, Any]) -> str | None:
    """Дата расчёта dashboard для legacy-снимков без dashboard_date_msk."""
    explicit = snapshot.get("dashboard_date_msk")
    if explicit:
        return str(explicit)
    coverage = snapshot.get("coverage_dashboard")
    if isinstance(coverage, dict) and coverage.get("as_of"):
        return str(coverage["as_of"])[:10]
    risks = snapshot.get("logistics_risks")
    if isinstance(risks, dict) and risks.get("as_of"):
        return str(risks["as_of"])[:10]
    analyzed_at = snapshot.get("analyzed_at")
    if analyzed_at:
        try:
            return datetime.fromisoformat(str(analyzed_at)).date().isoformat()
        except ValueError:
            pass
    return None


def is_dashboard_stale_for_today(snapshot: dict[str, Any]) -> bool:
    dashboard_date = derive_dashboard_date_msk(snapshot)
    if not dashboard_date:
        return True
    return dashboard_date != today_msk_iso()


def _scope_key(user_id: UUID | str | None) -> str:
    if user_id is None:
        return "anonymous"
    raw = str(user_id).strip()
    cleaned = _SAFE_SCOPE_RE.sub("_", raw).strip("._") or "anonymous"
    return cleaned[:120]


def _snapshot_path(user_id: UUID | str | None) -> Path:
    return _SNAPSHOT_DIR / f"{_scope_key(user_id)}.json"


def snapshot_had_valid_shift_today(user_id: UUID | str | None) -> bool:
    """Было ли у пользователя действующее сменное задание на сегодня (до перезаписи snapshot)."""
    path = _snapshot_path(user_id)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    shift = data.get("shift_assignment")
    if not isinstance(shift, dict):
        return False
    valid_date = shift.get("valid_date")
    if not valid_date:
        return False
    return str(valid_date) >= today_msk_iso()


def is_shift_assignment_valid(snapshot: dict[str, Any]) -> bool:
    """Сменное задание и дашборд задач доступны до 00:00 МСК следующего дня после valid_date."""
    shift = snapshot.get("shift_assignment")
    if not isinstance(shift, dict):
        return False
    valid_date = shift.get("valid_date")
    if not valid_date:
        return False
    return str(valid_date) >= today_msk_iso()


def _shift_valid_date(snapshot: dict[str, Any]) -> str | None:
    shift = snapshot.get("shift_assignment")
    if not isinstance(shift, dict):
        return None
    valid_date = shift.get("valid_date")
    return str(valid_date) if valid_date else None


def _expire_shift_day_snapshot(data: dict[str, Any]) -> bool:
    """Сбрасывает смену и задания после конца дня; True если snapshot изменился."""
    if is_shift_assignment_valid(data):
        data.pop("shift_day_expired", None)
        data.pop("shift_previous_valid_date", None)
        return False

    previous_date = _shift_valid_date(data)
    if not previous_date:
        task_dashboard = data.get("task_dashboard")
        if isinstance(task_dashboard, dict):
            meta = task_dashboard.get("meta")
            if isinstance(meta, dict) and meta.get("as_of"):
                previous_date = str(meta["as_of"])
    had_shift_payload = bool(data.get("shift_assignment") or data.get("task_dashboard"))
    data["shift_assignment"] = None
    data["task_dashboard"] = None
    data["shift_day_expired"] = True
    if previous_date:
        data["shift_previous_valid_date"] = previous_date
    return had_shift_payload or bool(previous_date)


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
    coverage_rebuild: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    refresh_inputs: dict[str, Any] | None = None,
    dashboard_date_msk: str | None = None,
    refresh_status: str | None = None,
    refresh_error: str | None = None,
    auto_refreshed_at: str | None = None,
    refresh_source_analyzed_at: str | None = None,
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
        "coverage_rebuild": coverage_rebuild,
        "meta": meta or {},
        "refresh_inputs": refresh_inputs,
        "dashboard_date_msk": dashboard_date_msk or today_msk_iso(),
        "refresh_status": refresh_status or "manual",
        "refresh_error": refresh_error,
        "auto_refreshed_at": auto_refreshed_at,
        "refresh_source_analyzed_at": refresh_source_analyzed_at,
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


def update_dashboard_refresh_state(
    user_id: UUID | str | None,
    *,
    refresh_status: str,
    refresh_error: str | None = None,
    refresh_attempted_date_msk: str | None = None,
) -> dict[str, Any] | None:
    """Фиксирует статус автопересчёта без изменения сохранённых дашбордов."""
    path = _snapshot_path(user_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    data["refresh_status"] = refresh_status
    data["refresh_error"] = refresh_error
    data["refresh_attempted_date_msk"] = refresh_attempted_date_msk or today_msk_iso()
    data["refresh_attempted_at"] = datetime.now(timezone.utc).isoformat()
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
    if not is_shift_assignment_valid(data):
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
    data["shift_today_msk"] = today_msk_iso()
    if not data.get("dashboard_date_msk"):
        derived_date = derive_dashboard_date_msk(data)
        if derived_date:
            data["dashboard_date_msk"] = derived_date
    if _expire_shift_day_snapshot(data):
        try:
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            logger.info(
                "document_analysis_agent.shift_day_expired",
                path=str(path),
                previous_valid_date=data.get("shift_previous_valid_date"),
            )
        except OSError as exc:
            logger.warning(
                "document_analysis_agent.shift_day_expire_persist_failed",
                path=str(path),
                error=str(exc),
            )
    return data


def clear_dashboard_snapshot(user_id: UUID | str | None) -> bool:
    path = _snapshot_path(user_id)
    if not path.is_file():
        return False
    path.unlink(missing_ok=True)
    return True


def coverage_dashboard_has_data(coverage: Any) -> bool:
    """True если в coverage_dashboard есть хотя бы одна позиция в изделиях или номенклатурах."""
    if not isinstance(coverage, dict):
        return False
    periods = coverage.get("periods")
    if not isinstance(periods, dict):
        return False
    for period in periods.values():
        if not isinstance(period, dict):
            continue
        for side_key in ("products", "nomenclatures"):
            side = period.get(side_key)
            if not isinstance(side, dict):
                continue
            tiles = side.get("tiles")
            if isinstance(tiles, dict) and int(tiles.get("all") or 0) > 0:
                return True
    return False


def snapshot_recency_key(snapshot: dict[str, Any]) -> str:
    for key in ("saved_at", "analyzed_at", "auto_refreshed_at", "progress_saved_at"):
        value = snapshot.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _read_snapshot_dict(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_latest_coverage_dashboard() -> dict[str, Any] | None:
    """Последний coverage_dashboard из любого пользовательского снимка."""
    if not _SNAPSHOT_DIR.is_dir():
        return None
    best_key = ""
    best_coverage: dict[str, Any] | None = None
    for path in _SNAPSHOT_DIR.glob("*.json"):
        data = _read_snapshot_dict(path)
        if not data:
            continue
        coverage = data.get("coverage_dashboard")
        if not coverage_dashboard_has_data(coverage):
            continue
        recency = snapshot_recency_key(data)
        if recency >= best_key:
            best_key = recency
            best_coverage = coverage if isinstance(coverage, dict) else None
    return best_coverage
