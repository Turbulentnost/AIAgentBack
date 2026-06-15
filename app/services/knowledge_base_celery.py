from __future__ import annotations

from typing import Any

from app.workers.celery_app import celery_app

_INDEXING_TASK_NAMES = {
    "index_knowledge_base_full",
    "index_knowledge_base_source",
    "reindex_knowledge_base_embeddings",
    "reindex_knowledge_base_after_access_change",
}


def attach_celery_task_id(processing_params: dict[str, Any] | None, celery_task_id: str) -> dict[str, Any]:
    params = dict(processing_params or {})
    params["celery_task_id"] = celery_task_id
    return params


def read_celery_task_id(processing_params: dict[str, Any] | None) -> str | None:
    if not processing_params:
        return None
    task_id = processing_params.get("celery_task_id")
    return str(task_id) if task_id else None


def revoke_indexing_celery_task(celery_task_id: str | None, *, terminate: bool = False) -> None:
    if not celery_task_id:
        return
    celery_app.control.revoke(celery_task_id, terminate=terminate, signal="SIGTERM")


def revoke_active_indexing_tasks(
    *,
    knowledge_base_id: str | None = None,
    job_ids: list[str] | None = None,
    terminate: bool = False,
) -> list[str]:
    """Отзывает активные задачи индексации по ID базы и/или job_id (из args Celery)."""
    revoked: set[str] = set()
    job_id_set = {str(item) for item in (job_ids or []) if item}
    try:
        inspect = celery_app.control.inspect(timeout=2.0)
        active_tasks = inspect.active() or {}
    except Exception:
        active_tasks = {}

    for tasks in active_tasks.values():
        for task in tasks:
            task_name = str(task.get("name") or "")
            if task_name not in _INDEXING_TASK_NAMES:
                continue
            args = [str(item) for item in (task.get("args") or [])]
            task_id = str(task.get("id") or "")
            if not task_id:
                continue
            matches_kb = knowledge_base_id is not None and knowledge_base_id in args
            matches_job = bool(job_id_set.intersection(args))
            if matches_kb or matches_job:
                revoke_indexing_celery_task(task_id, terminate=terminate)
                revoked.add(task_id)
    return sorted(revoked)
