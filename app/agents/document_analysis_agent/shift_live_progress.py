"""Live-прогресс смены менеджера из dashboard snapshots (до «Завершить смену»)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from app.agents.document_analysis_agent.dashboard_snapshot import (
    _SNAPSHOT_DIR,
    coverage_dashboard_has_data,
    is_shift_assignment_valid,
    load_latest_coverage_dashboard,
    today_msk_iso,
)
from app.agents.document_analysis_agent.shift_assignment import (
    SHIFT_MANAGER_EMAILS,
    SHIFT_MANAGER_REGIONS,
    SHIFT_MANAGER_ROSTER,
)

MANAGER_COLUMN = "Ответственный менеджер"
RESULT_COLUMN = "Результат работы менеджера"


def build_task_key(task_type: str, nomenclature: str, problem: str, solution: str) -> str:
    return "::".join(
        [
            task_type,
            nomenclature,
            (problem or "")[:120],
            (solution or "")[:80],
        ]
    )


def _read_snapshot_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def iter_valid_shift_snapshots() -> list[dict[str, Any]]:
    if not _SNAPSHOT_DIR.is_dir():
        return []
    snapshots: list[dict[str, Any]] = []
    for path in sorted(_SNAPSHOT_DIR.glob("*.json")):
        data = _read_snapshot_file(path)
        if not data or not is_shift_assignment_valid(data):
            continue
        task_dashboard = data.get("task_dashboard")
        if not isinstance(task_dashboard, dict):
            continue
        values = task_dashboard.get("values")
        if not isinstance(values, list) or len(values) <= 1:
            continue
        snapshots.append(data)
    return snapshots


def _snapshot_sort_key(snapshot: dict[str, Any]) -> str:
    for key in ("progress_saved_at", "saved_at", "analyzed_at"):
        value = snapshot.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _build_task_types_by_row(body: list[list[Any]], row_kinds: list[str]) -> list[str]:
    types: list[str] = []
    current_type = ""
    for index, row in enumerate(body):
        kind = row_kinds[index] if index < len(row_kinds) else "task"
        if kind == "group":
            current_type = str((row[0] if row else "") or "").strip()
            types.append("")
            continue
        if kind == "task":
            types.append(current_type)
            continue
        types.append("")
    return types


def _filter_task_dashboard_for_manager(
    task_dashboard: dict[str, Any],
    manager_name: str,
) -> dict[str, Any] | None:
    values = task_dashboard.get("values")
    row_kinds = task_dashboard.get("row_kinds")
    row_priorities = task_dashboard.get("row_priorities")
    if not isinstance(values, list) or len(values) <= 1:
        return None
    if not isinstance(row_kinds, list) or not isinstance(row_priorities, list):
        return None

    header = [str(cell or "") for cell in values[0]]
    body = values[1:]
    body_kinds = [str(kind or "task") for kind in row_kinds[1:]]
    body_priorities = row_priorities[1:]

    manager_col = header.index(MANAGER_COLUMN) if MANAGER_COLUMN in header else -1
    if manager_col < 0:
        return None

    filtered_body: list[list[Any]] = []
    filtered_kinds: list[str] = [str(row_kinds[0] or "header")]
    filtered_priorities: list[Any] = [row_priorities[0] if row_priorities else None]
    pending_group: tuple[list[Any], Any] | None = None

    def flush_group() -> None:
        nonlocal pending_group
        if pending_group is None:
            return
        row, priority = pending_group
        filtered_body.append(row)
        filtered_kinds.append("group")
        filtered_priorities.append(priority)
        pending_group = None

    for index, row in enumerate(body):
        kind = body_kinds[index] if index < len(body_kinds) else "task"
        priority = body_priorities[index] if index < len(body_priorities) else None
        if kind == "group":
            flush_group()
            pending_group = (row, priority)
            continue
        if kind != "task":
            continue
        manager = str((row[manager_col] if manager_col < len(row) else "") or "").strip()
        if manager != manager_name:
            continue
        if pending_group is not None:
            flush_group()
        filtered_body.append(row)
        filtered_kinds.append("task")
        filtered_priorities.append(priority)

    flush_group()
    if not any(kind == "task" for kind in filtered_kinds):
        return None

    return {
        **task_dashboard,
        "values": [header, *filtered_body],
        "row_kinds": filtered_kinds,
        "row_priorities": filtered_priorities,
    }


def _progress_status(task_key: str, result_evals: dict[str, Any]) -> str:
    eval_state = result_evals.get(task_key)
    if not isinstance(eval_state, dict):
        return "active"
    if eval_state.get("loading"):
        return "active"
    status = eval_state.get("status")
    if status in {"resolved", "partial", "not_resolved"}:
        return str(status)
    return "active"


def build_manager_live_payload(
    task_dashboard: dict[str, Any],
    *,
    manager_name: str,
    report_date: date,
    progress_saved_at: str | None = None,
    source_user_id: str | None = None,
) -> dict[str, Any] | None:
    filtered = _filter_task_dashboard_for_manager(task_dashboard, manager_name)
    if filtered is None:
        return None

    values = filtered.get("values") or []
    row_kinds = [str(kind or "task") for kind in (filtered.get("row_kinds") or [])]
    row_priorities = filtered.get("row_priorities") or []
    result_texts = filtered.get("result_texts") if isinstance(filtered.get("result_texts"), dict) else {}
    result_evals = filtered.get("result_evals") if isinstance(filtered.get("result_evals"), dict) else {}

    header = [str(cell or "") for cell in (values[0] if values else [])]
    body = values[1:] if len(values) > 1 else []
    body_kinds = row_kinds[1:]
    col_index = {title: index for index, title in enumerate(header)}
    task_types = _build_task_types_by_row(body, body_kinds)

    stats = {
        "total": 0,
        "resolved": 0,
        "incomplete": 0,
        "partial": 0,
        "not_resolved": 0,
        "active": 0,
        "resolved_percent": 0,
    }
    tasks: list[dict[str, Any]] = []

    for index, row in enumerate(body):
        if (body_kinds[index] if index < len(body_kinds) else "task") != "task":
            continue
        task_type = task_types[index] if index < len(task_types) else ""
        problem = str(row[col_index.get("Проблема", -1)] if col_index.get("Проблема", -1) >= 0 else "")
        solution = str(row[col_index.get("Что сделать", -1)] if col_index.get("Что сделать", -1) >= 0 else "")
        nomenclature = str(row[col_index.get("Номенклатура", -1)] if col_index.get("Номенклатура", -1) >= 0 else "")
        task_key = build_task_key(task_type, nomenclature, problem, solution)
        status = _progress_status(task_key, result_evals)
        eval_state = result_evals.get(task_key) if isinstance(result_evals.get(task_key), dict) else {}
        result_col = col_index.get(RESULT_COLUMN, -1)
        result_text = str(result_texts.get(task_key) or (row[result_col] if result_col >= 0 else "") or "")

        priority_index = index + 1
        priority = (
            str(row_priorities[priority_index])
            if priority_index < len(row_priorities) and row_priorities[priority_index]
            else "week"
        )

        task_payload = {
            "key": task_key,
            "task_type": task_type,
            "nomenclature": nomenclature,
            "problem": problem,
            "solution": solution,
            "priority": priority,
            "deadline": str(row[col_index.get("Крайний срок", -1)] if col_index.get("Крайний срок", -1) >= 0 else ""),
            "deficit": str(row[col_index.get("Дефицит", -1)] if col_index.get("Дефицит", -1) >= 0 else ""),
            "unit": str(row[col_index.get("Ед. изм.", -1)] if col_index.get("Ед. изм.", -1) >= 0 else ""),
            "status": status,
            "result_text": result_text,
            "eval_comment": str(eval_state.get("comment") or ""),
            "reason": "",
        }
        tasks.append(task_payload)

        stats["total"] += 1
        if status == "resolved":
            stats["resolved"] += 1
        elif status == "partial":
            stats["partial"] += 1
        elif status == "not_resolved":
            stats["not_resolved"] += 1
        else:
            stats["active"] += 1

    if stats["total"] == 0:
        return None

    stats["incomplete"] = stats["partial"] + stats["not_resolved"] + stats["active"]
    stats["resolved_percent"] = round((stats["resolved"] / stats["total"]) * 100)

    return {
        "id": f"live:{manager_name}:{report_date.isoformat()}",
        "manager_name": manager_name,
        "report_date": report_date.isoformat(),
        "report_status": "in_progress",
        "region_label": SHIFT_MANAGER_REGIONS.get(manager_name, ""),
        "stats": stats,
        "tasks": tasks,
        "incomplete_tasks": [task for task in tasks if task.get("status") != "resolved"],
        "email_sent_to": "",
        "email_sent_at": None,
        "live_updated_at": progress_saved_at,
        "live_source_user_id": source_user_id,
    }


def _merge_evals(
    base_dashboard: dict[str, Any],
    overlay_dashboard: dict[str, Any] | None,
) -> dict[str, Any]:
    if overlay_dashboard is None:
        return base_dashboard
    merged = dict(base_dashboard)
    base_evals = dict(base_dashboard.get("result_evals") or {})
    overlay_evals = overlay_dashboard.get("result_evals")
    overlay_texts = overlay_dashboard.get("result_texts")
    if isinstance(overlay_evals, dict):
        base_evals.update({str(k): v for k, v in overlay_evals.items() if isinstance(v, dict)})
    merged["result_evals"] = base_evals
    if isinstance(overlay_texts, dict):
        base_texts = dict(base_dashboard.get("result_texts") or {})
        base_texts.update({str(k): str(v) for k, v in overlay_texts.items()})
        merged["result_texts"] = base_texts
    return merged


def resolve_manager_live_report(
    manager_name: str,
    report_date: date,
    *,
    manager_user_id: UUID | str | None = None,
) -> dict[str, Any] | None:
    if report_date.isoformat() != today_msk_iso():
        return None

    snapshots = sorted(iter_valid_shift_snapshots(), key=_snapshot_sort_key, reverse=True)
    if not snapshots:
        return None

    overlay: dict[str, Any] | None = None
    overlay_saved_at: str | None = None
    if manager_user_id is not None:
        manager_id = str(manager_user_id)
        for snapshot in snapshots:
            if str(snapshot.get("user_id") or "") != manager_id:
                continue
            task_dashboard = snapshot.get("task_dashboard")
            if isinstance(task_dashboard, dict):
                overlay = task_dashboard
                overlay_saved_at = str(
                    snapshot.get("progress_saved_at") or snapshot.get("saved_at") or ""
                )
                break

    for snapshot in snapshots:
        task_dashboard = snapshot.get("task_dashboard")
        if not isinstance(task_dashboard, dict):
            continue
        merged_dashboard = _merge_evals(task_dashboard, overlay)
        payload = build_manager_live_payload(
            merged_dashboard,
            manager_name=manager_name,
            report_date=report_date,
            progress_saved_at=overlay_saved_at
            or str(snapshot.get("progress_saved_at") or snapshot.get("saved_at") or ""),
            source_user_id=str(snapshot.get("user_id") or "") or None,
        )
        if payload is not None:
            if overlay_saved_at:
                payload["live_updated_at"] = overlay_saved_at
            if manager_user_id is not None:
                payload["live_source_user_id"] = str(manager_user_id)
            return payload

    return None


def has_any_live_shift_for_today() -> bool:
    today = date.fromisoformat(today_msk_iso())
    for manager_name in SHIFT_MANAGER_ROSTER:
        if resolve_manager_live_report(manager_name, today) is not None:
            return True
    return False


def _task_dashboard_has_rows(task_dashboard: object) -> bool:
    if not isinstance(task_dashboard, dict):
        return False
    values = task_dashboard.get("values")
    return isinstance(values, list) and len(values) > 1


def enrich_manager_dashboard_snapshot(
    snapshot: dict[str, Any],
    *,
    manager_name: str,
    manager_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Дополняет снимок менеджера общим coverage и сменным заданием из последнего анализа."""
    enriched = dict(snapshot)
    own_task_overlay: dict[str, Any] | None = None
    if manager_user_id is not None and str(enriched.get("user_id") or "") == str(manager_user_id):
        task_dashboard = enriched.get("task_dashboard")
        if isinstance(task_dashboard, dict) and (
            task_dashboard.get("result_evals") or task_dashboard.get("result_texts")
        ):
            own_task_overlay = task_dashboard

    if not coverage_dashboard_has_data(enriched.get("coverage_dashboard")):
        shared_coverage = load_latest_coverage_dashboard()
        if shared_coverage:
            enriched["coverage_dashboard"] = shared_coverage
            enriched["coverage_dashboard_shared"] = True

    task_dashboard = enriched.get("task_dashboard")
    if not _task_dashboard_has_rows(task_dashboard):
        snapshots = sorted(iter_valid_shift_snapshots(), key=_snapshot_sort_key, reverse=True)
        for candidate in snapshots:
            shared_tasks = candidate.get("task_dashboard")
            if not _task_dashboard_has_rows(shared_tasks):
                continue
            merged_tasks = _merge_evals(
                shared_tasks if isinstance(shared_tasks, dict) else {},
                own_task_overlay,
            )
            enriched["task_dashboard"] = merged_tasks
            shift_assignment = candidate.get("shift_assignment")
            if isinstance(shift_assignment, dict) and not enriched.get("shift_assignment"):
                enriched["shift_assignment"] = shift_assignment
            break
    elif isinstance(task_dashboard, dict):
        enriched["task_dashboard"] = _merge_evals(task_dashboard, own_task_overlay)

    current_tasks = enriched.get("task_dashboard")
    if _task_dashboard_has_rows(current_tasks) and isinstance(current_tasks, dict):
        filtered = _filter_task_dashboard_for_manager(current_tasks, manager_name)
        if filtered is not None:
            enriched["task_dashboard"] = filtered

    return enriched
