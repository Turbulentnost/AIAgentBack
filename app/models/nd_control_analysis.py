from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import DepartmentAnalysisRunStatus, DepartmentAnalysisStep


class DepartmentAnalysisRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "nd_department_analysis_runs"

    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nd_control_departments.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[DepartmentAnalysisRunStatus] = mapped_column(
        default=DepartmentAnalysisRunStatus.PENDING,
        index=True,
    )
    current_step: Mapped[DepartmentAnalysisStep] = mapped_column(
        default=DepartmentAnalysisStep.INITIALIZING,
        index=True,
    )
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    total_knowledge_bases: Mapped[int] = mapped_column(Integer, default=0)
    total_documents: Mapped[int] = mapped_column(Integer, default=0)
    processed_documents: Mapped[int] = mapped_column(Integer, default=0)
    skipped_documents: Mapped[int] = mapped_column(Integer, default=0)
    failed_documents: Mapped[int] = mapped_column(Integer, default=0)
    needs_review_documents: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(index=True)
    finished_at: Mapped[datetime | None] = mapped_column(index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    summary_json: Mapped[dict | None] = mapped_column(JSONB)
    celery_task_id: Mapped[str | None] = mapped_column(String(128), index=True)

    department: Mapped["NdControlDepartment"] = relationship()


from app.models.nd_control_registry import NdControlDepartment  # noqa: E402
