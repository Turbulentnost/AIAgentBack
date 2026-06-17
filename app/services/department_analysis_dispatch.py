from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.enums import DepartmentAnalysisRunStatus, DepartmentAnalysisStep
from app.models.nd_control_analysis import DepartmentAnalysisRun
from app.services.department_analysis_service import DepartmentAnalysisService

logger = get_logger(__name__)

STALE_PENDING_SECONDS = 30
STALE_RUNNING_SECONDS = 900
CELERY_TASK_NAME = "run_department_analysis"


def is_celery_department_analysis_available() -> bool:
    try:
        from app.workers.celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=2.0)
        if inspect is None:
            return False
        registered = inspect.registered() or {}
        return any(CELERY_TASK_NAME in tasks for tasks in registered.values())
    except Exception as exc:
        logger.warning("nd_control.analysis.celery_inspect_failed", error=str(exc))
        return False


async def run_department_analysis_background(run_id: uuid.UUID, force_reextract: bool) -> None:
    async with AsyncSessionLocal() as session:
        try:
            await DepartmentAnalysisService(session).execute_department_analysis(
                run_id,
                force_reextract=force_reextract,
            )
            await session.commit()
        except Exception:
            logger.exception("nd_control.analysis.background_failed", run_id=str(run_id))
            await session.rollback()
            raise


async def enqueue_department_analysis_run(
    db: AsyncSession,
    run: DepartmentAnalysisRun,
    force_reextract: bool,
    background_tasks: BackgroundTasks,
) -> None:
    celery_task_id: str | None = None
    if is_celery_department_analysis_available():
        try:
            from app.workers.tasks import run_department_analysis

            result = run_department_analysis.delay(str(run.id), force_reextract)
            celery_task_id = result.id
            logger.info(
                "nd_control.analysis.enqueued_celery",
                run_id=str(run.id),
                celery_task_id=celery_task_id,
            )
        except Exception as exc:
            logger.warning(
                "nd_control.analysis.celery_enqueue_failed",
                run_id=str(run.id),
                error=str(exc),
            )

    if celery_task_id:
        run.celery_task_id = celery_task_id
        await db.flush()
        return

    logger.info("nd_control.analysis.enqueued_background", run_id=str(run.id))
    background_tasks.add_task(run_department_analysis_background, run.id, force_reextract)


def is_stale_pending_run(run: DepartmentAnalysisRun) -> bool:
    if run.status != DepartmentAnalysisRunStatus.PENDING:
        return False
    if run.started_at is not None:
        return False
    created = run.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created).total_seconds()
    return age >= STALE_PENDING_SECONDS


def is_stale_running_run(run: DepartmentAnalysisRun) -> bool:
    if run.status != DepartmentAnalysisRunStatus.RUNNING:
        return False
    reference = run.started_at or run.created_at
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - reference).total_seconds()
    return age >= STALE_RUNNING_SECONDS


async def maybe_recover_stale_pending_run(
    db: AsyncSession,
    run: DepartmentAnalysisRun,
    background_tasks: BackgroundTasks,
) -> bool:
    if is_stale_pending_run(run):
        force_reextract = bool((run.summary_json or {}).get("force_reextract", False))
        logger.warning("nd_control.analysis.recover_stale_pending", run_id=str(run.id))
        await enqueue_department_analysis_run(db, run, force_reextract, background_tasks)
        await db.commit()
        return True

    if not is_stale_running_run(run):
        return False

    force_reextract = bool((run.summary_json or {}).get("force_reextract", False))
    logger.warning("nd_control.analysis.recover_stale_running", run_id=str(run.id))
    run.status = DepartmentAnalysisRunStatus.FAILED
    run.current_step = DepartmentAnalysisStep.FAILED
    run.error_message = "Анализ прерван или завис. Запускаем повторно."
    run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()
    new_run = DepartmentAnalysisRun(
        department_id=run.department_id,
        status=DepartmentAnalysisRunStatus.PENDING,
        current_step=DepartmentAnalysisStep.INITIALIZING,
        progress_percent=0,
        summary_json={"force_reextract": force_reextract, "recovered_from_run_id": str(run.id)},
    )
    db.add(new_run)
    await db.flush()
    await enqueue_department_analysis_run(db, new_run, force_reextract, background_tasks)
    await db.commit()
    return True
