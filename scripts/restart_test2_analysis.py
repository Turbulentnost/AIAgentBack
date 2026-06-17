"""Restart department analysis for Test 2."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.models.enums import DepartmentAnalysisRunStatus
from app.services.department_analysis_dispatch import enqueue_department_analysis_run
from app.services.department_analysis_service import DepartmentAnalysisService

DEPT_ID = uuid.UUID("b6ea8bf1-7cc5-4dec-a81f-42a4cc682d6c")


class _NoopBackgroundTasks:
    def add_task(self, *args, **kwargs) -> None:
        pass


async def main() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE nd_department_analysis_runs SET status = 'FAILED', "
                "error_message = 'Прервано для перезапуска', finished_at = NOW() "
                "WHERE department_id = :did AND status IN ('PENDING', 'RUNNING')"
            ),
            {"did": DEPT_ID},
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        service = DepartmentAnalysisService(db)
        run = await service.start_department_analysis(DEPT_ID, force_reextract=False)
        await enqueue_department_analysis_run(db, run, False, _NoopBackgroundTasks())
        await db.commit()
        print(f"Started run {run.id} for Test 2")


if __name__ == "__main__":
    asyncio.run(main())
