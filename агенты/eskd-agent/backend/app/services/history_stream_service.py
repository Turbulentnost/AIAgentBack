"""Инкрементальное сохранение проверки в историю во время SSE-stream."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.gost.aggregation import aggregate_from_check_response
from app.gost.catalog import GOST_LINE_KEYS
from app.services.history_service import HistoryService
from app.services.user_service import EskdActor

_log = logging.getLogger("eskd.history.stream")


def _empty_gost_summary() -> dict[str, Any]:
    return {"passed": list(GOST_LINE_KEYS), "warnings": {}, "errors": {}}


def _payload_stats(items: list[dict[str, Any]]) -> tuple[int, int, int]:
    processed = sum(1 for row in items if isinstance(row, dict) and not row.get("error"))
    total_errors = sum(int(row.get("errors_count") or 0) for row in items if isinstance(row, dict))
    total_warnings = sum(int(row.get("warnings_count") or 0) for row in items if isinstance(row, dict))
    return processed, total_errors, total_warnings


def new_stream_state() -> dict[str, Any]:
    return {
        "run_id": None,
        "job_id": None,
        "total_items": 0,
        "items": [],
    }


def build_partial_payload(state: dict[str, Any], *, status: str = "running") -> dict[str, Any]:
    items = [row for row in state.get("items") or [] if row]
    processed, total_errors, total_warnings = _payload_stats(items)
    total = int(state.get("total_items") or len(items) or 0)
    progress = round(100 * processed / total, 1) if total else 0.0
    payload: dict[str, Any] = {
        "job_id": state.get("job_id"),
        "status": status,
        "items": items,
        "total_items": total,
        "processed": processed,
        "failed": sum(1 for row in items if row.get("error")),
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "progress_percent": progress,
    }
    payload["gost_summary"] = aggregate_from_check_response(payload)
    return payload


async def handle_stream_history_event(
    db: AsyncSession,
    payload: dict[str, Any],
    *,
    state: dict[str, Any],
    uploads: list[tuple[str, bytes]],
    check_params: dict[str, Any] | None,
    actor: EskdActor | None,
) -> uuid.UUID | None:
    event_type = payload.get("type")
    service = HistoryService(db)

    if event_type == "start":
        state["job_id"] = str(payload.get("job_id") or "")
        state["total_items"] = int(payload.get("total") or 0)
        if not state["job_id"]:
            return None
        existing = await service.find_by_job_id(state["job_id"])
        if existing:
            state["run_id"] = existing.id
            return existing.id
        run = await service.create_running_run(
            job_id=state["job_id"],
            uploads=uploads,
            check_params=check_params,
            actor=actor,
            total_items=state["total_items"],
        )
        state["run_id"] = run.id
        return run.id

    if event_type == "item":
        item = payload.get("item")
        if not isinstance(item, dict):
            return state.get("run_id")
        index = int(item.get("index") or len(state["items"]) + 1)
        items: list[dict[str, Any]] = state.setdefault("items", [])
        while len(items) < index:
            items.append({})
        items[index - 1] = item
        if not state.get("run_id") and state.get("job_id"):
            await handle_stream_history_event(
                db,
                {"type": "start", "job_id": state["job_id"], "total": state.get("total_items")},
                state=state,
                uploads=uploads,
                check_params=check_params,
                actor=actor,
            )
        run_id = state.get("run_id")
        if not run_id:
            return None
        partial = build_partial_payload(state, status="running")
        if payload.get("total"):
            partial["total_items"] = int(payload["total"])
        await service.update_run_progress(run_id, partial)
        return run_id

    if event_type == "progress":
        if payload.get("total"):
            state["total_items"] = int(payload["total"])
        run_id = state.get("run_id")
        if not run_id:
            return None
        partial = build_partial_payload(state, status="running")
        partial["progress_percent"] = float(payload.get("percent") or partial.get("progress_percent") or 0)
        await service.update_run_progress(run_id, partial)
        return run_id

    return state.get("run_id")


async def finalize_stream_history(
    db: AsyncSession,
    *,
    state: dict[str, Any],
    payload: dict[str, Any],
    uploads: list[tuple[str, bytes]],
    check_params: dict[str, Any] | None,
    actor: EskdActor | None,
) -> uuid.UUID | None:
    service = HistoryService(db)
    run_id = state.get("run_id")
    if run_id:
        run = await service.finalize_run(
            run_id,
            payload=payload,
            uploads=uploads,
            check_params=check_params,
            actor=actor,
        )
        return run.id if run else run_id
    run = await service.save_check_run(
        payload=payload,
        uploads=uploads,
        check_params=check_params,
        actor=actor,
    )
    return run.id if run else None
