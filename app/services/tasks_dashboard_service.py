from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tasks_agent.backend import TasksBackend, TasksBackendError
from app.agents.tasks_agent.config import DEFAULT_PORUCHENIYA_LIMIT
from app.agents.tasks_agent.metrics_presenter import build_tasks_metrics
from app.agents.tasks_agent.table_presenter import build_tasks_table
from app.models.user import User
from app.schemas.porucheniya import (
    TasksDashboardCountsRead,
    TasksDashboardRead,
    TasksMetricsRead,
    TasksMetricsRowRead,
    TasksTableColumnRead,
    TasksTableRead,
)
from app.services.tasks_manager_resolver import resolve_porucheniya_manager_fio
from app.agents.tasks_agent.table_presenter import PORUCHENIYA_TASK_TABLE_COLUMNS
from app.tools.onec.get_porucheniya import parse_input_date, resolve_porucheniya_period


class TasksDashboardServiceError(ValueError):
    pass


def _manager_lookup_error_message(exc: Exception) -> str | None:
    message = str(exc).strip()
    if not message:
        return None
    if "Пользователь не найден" in message:
        return (
            f"{message}. Проверьте ФИО в профиле пользователя или обратитесь к администратору 1С."
        )
    if "Не удалось определить ФИО руководителя" in message:
        return message
    if "Не удалось определить ключ руководителя" in message:
        return (
            f"{message}. Проверьте, что пользователь заведён в справочнике 1С "
            "и ФИО совпадает с профилем в системе."
        )
    return None


def _empty_metrics_read(*, summary_note: str, period_end: str, fetched_at: datetime) -> TasksMetricsRead:
    report_day = parse_input_date(period_end or None, default=fetched_at.date())
    metrics = build_tasks_metrics(
        [],
        [],
        summary_note=summary_note,
        report_day=report_day,
        now=fetched_at,
    )
    return TasksMetricsRead(
        rows=[TasksMetricsRowRead(**row) for row in metrics.get("rows") or []],
        report_day=str(metrics.get("report_day") or report_day.isoformat()),
    )


def _strip_internal_row_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in row.items() if not key.startswith("_")} for row in rows]


def _build_priority_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in items:
        priority = str(item.get("priority") or "unknown")
        summary[priority] = summary.get(priority, 0) + 1
    return summary


def _build_summary(counts: dict[str, int]) -> str:
    return (
        f"Загружено: поручений {counts.get('porucheniya_documents', 0)} "
        f"({counts.get('porucheniya_tasks', 0)} мероприятий), "
        f"протоколов {counts.get('protocol_documents', 0)} "
        f"({counts.get('protocol_tasks', 0)} задач)"
    )


def _build_table_read(table: dict[str, Any]) -> TasksTableRead:
    columns = [
        TasksTableColumnRead(key=column["key"], title=column["title"])
        for column in table.get("columns") or []
    ]
    rows = _strip_internal_row_fields(table.get("rows") or [])
    return TasksTableRead(
        columns=columns,
        rows=rows,
        row_count=table.get("row_count", len(rows)),
    )


def _build_metrics_read(
    porucheniya: list[dict[str, Any]],
    protocols: list[dict[str, Any]],
    *,
    summary_note: str,
    period_end: str,
    fetched_at: datetime,
) -> TasksMetricsRead:
    report_day = parse_input_date(period_end or None, default=fetched_at.date())
    metrics = build_tasks_metrics(
        porucheniya,
        protocols,
        summary_note=summary_note,
        report_day=report_day,
        now=fetched_at,
    )
    return TasksMetricsRead(
        rows=[TasksMetricsRowRead(**row) for row in metrics.get("rows") or []],
        report_day=str(metrics.get("report_day") or report_day.isoformat()),
    )


class TasksDashboardService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _empty_dashboard(
        self,
        *,
        author_fio: str,
        manager_fio_source: str,
        period_start: str | None,
        period_end: str | None,
        error: str,
    ) -> TasksDashboardRead:
        start, end = resolve_porucheniya_period(period_start, period_end)
        fetched_at = datetime.now(timezone.utc)
        summary_note = "Данные не загружены"
        period_end_value = end.isoformat()
        return TasksDashboardRead(
            author_fio=author_fio,
            manager_fio_source=manager_fio_source,
            period_start=start.isoformat(),
            period_end=period_end_value,
            counts=TasksDashboardCountsRead(),
            priority_summary={},
            metrics=_empty_metrics_read(
                summary_note=summary_note,
                period_end=period_end_value,
                fetched_at=fetched_at,
            ),
            tasks_table=TasksTableRead(
                columns=[
                    TasksTableColumnRead(key=column["key"], title=column["title"])
                    for column in PORUCHENIYA_TASK_TABLE_COLUMNS
                ],
                rows=[],
                row_count=0,
            ),
            summary=summary_note,
            fetched_at=fetched_at,
            error=error,
        )

    async def load_dashboard(
        self,
        current_user: User,
        *,
        period_start: str | None = None,
        period_end: str | None = None,
        limit: int = DEFAULT_PORUCHENIYA_LIMIT,
    ) -> TasksDashboardRead:
        try:
            author_fio, manager_fio_source = await resolve_porucheniya_manager_fio(
                self.db,
                current_user,
            )
        except ValueError as exc:
            lookup_error = _manager_lookup_error_message(exc)
            if lookup_error:
                return self._empty_dashboard(
                    author_fio="",
                    manager_fio_source="unknown",
                    period_start=period_start,
                    period_end=period_end,
                    error=lookup_error,
                )
            raise TasksDashboardServiceError(str(exc)) from exc

        backend = TasksBackend(self.db)
        try:
            payload = await backend.load_porucheniya(
                period_start=period_start,
                period_end=period_end,
                limit=limit,
                current_user=current_user,
            )
        except TasksBackendError as exc:
            lookup_error = _manager_lookup_error_message(exc)
            if lookup_error:
                return self._empty_dashboard(
                    author_fio=author_fio,
                    manager_fio_source=manager_fio_source,
                    period_start=period_start,
                    period_end=period_end,
                    error=lookup_error,
                )
            raise TasksDashboardServiceError(str(exc)) from exc

        table = build_tasks_table(
            payload.get("porucheniya") or [],
            payload.get("protocols") or [],
        )
        counts_raw = payload.get("counts") or {}
        counts = TasksDashboardCountsRead(
            porucheniya_documents=counts_raw.get("porucheniya_documents", 0),
            porucheniya_tasks=counts_raw.get("porucheniya_tasks", 0),
            protocol_documents=counts_raw.get("protocol_documents", 0),
            protocol_tasks=counts_raw.get("protocol_tasks", 0),
            total_tasks=counts_raw.get("total_tasks", 0),
        )
        fetched_at = datetime.now(timezone.utc)
        summary_note = _build_summary(counts_raw)
        period_end_value = str(payload.get("period_end") or period_end or "")

        return TasksDashboardRead(
            author_fio=author_fio,
            manager_fio_source=manager_fio_source,
            period_start=str(payload.get("period_start") or period_start or ""),
            period_end=period_end_value,
            counts=counts,
            priority_summary=_build_priority_summary(payload.get("items") or []),
            metrics=_build_metrics_read(
                payload.get("porucheniya") or [],
                payload.get("protocols") or [],
                summary_note=summary_note,
                period_end=period_end_value,
                fetched_at=fetched_at,
            ),
            tasks_table=_build_table_read(table),
            summary=summary_note,
            fetched_at=fetched_at,
        )
