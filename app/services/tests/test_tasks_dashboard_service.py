from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.tasks_agent.backend import TasksBackendError
from app.services.tasks_dashboard_service import TasksDashboardService


@pytest.mark.asyncio
async def test_load_dashboard_returns_empty_state_when_manager_missing_in_onec() -> None:
    user = AsyncMock()
    service = TasksDashboardService(db=AsyncMock())

    with patch(
        "app.services.tasks_dashboard_service.resolve_porucheniya_manager_fio",
        AsyncMock(return_value=("Иванов Иван Иванович", "user_full_name")),
    ):
        with patch(
            "app.services.tasks_dashboard_service.TasksBackend.load_porucheniya",
            AsyncMock(
                side_effect=TasksBackendError('Пользователь не найден: «Иванов Иван Иванович»')
            ),
        ):
            result = await service.load_dashboard(user)

    assert result.error
    assert "Иванов Иван Иванович" in result.error
    assert result.tasks_table.row_count == 0
    assert result.counts.total_tasks == 0


@pytest.mark.asyncio
async def test_load_dashboard_builds_tasks_table_for_current_user() -> None:
    payload = {
        "period_start": "2026-05-01",
        "period_end": "2026-06-18",
        "counts": {
            "porucheniya_documents": 1,
            "porucheniya_tasks": 1,
            "protocol_documents": 1,
            "protocol_tasks": 1,
            "total_tasks": 2,
        },
        "porucheniya": [
            {
                "document_number": "АСТ00-00039",
                "document_date": "2026-05-29T15:12:24",
                "status": "ВРаботе",
                "reviewer": "Ильченко Екатерина Александровна",
                "tasks": [
                    {
                        "activity": "Задача 1",
                        "responsible": "Исполнитель 1",
                        "department": "Департамент",
                        "due_date": "2026-06-02T00:00:00",
                        "has_file": "Нет",
                        "priority": "Высокий",
                    }
                ],
            }
        ],
        "protocols": [],
        "items": [{"priority": "Высокий"}, {"priority": "Средний"}],
    }
    user = AsyncMock()
    service = TasksDashboardService(db=AsyncMock())

    with patch(
        "app.services.tasks_dashboard_service.resolve_porucheniya_manager_fio",
        AsyncMock(return_value=("Амураль Игорь Борисович", "user_full_name")),
    ):
        with patch(
            "app.services.tasks_dashboard_service.TasksBackend.load_porucheniya",
            AsyncMock(return_value=payload),
        ):
            result = await service.load_dashboard(user, period_start="2026-05-01", period_end="2026-06-18")

    assert result.author_fio == "Амураль Игорь Борисович"
    assert result.manager_fio_source == "user_full_name"
    assert result.tasks_table.row_count == 1
    assert result.tasks_table.rows[0]["task_text"] == "Задача 1"
    assert "_meta" not in result.tasks_table.rows[0]
    assert result.priority_summary == {"Высокий": 1, "Средний": 1}
    assert result.metrics.rows[0].key == "total_under_control"
    assert result.metrics.rows[0].count == 1
    assert result.metrics.rows[0].note == result.summary
    assert result.metrics.report_day == "2026-06-18"
    assert result.counts.total_tasks == 2
    assert isinstance(result.fetched_at, datetime)
    assert result.fetched_at.tzinfo == timezone.utc
