from datetime import datetime

from app.agents.tasks_agent.metrics_presenter import build_tasks_metrics


def test_build_tasks_metrics_matches_summary_table() -> None:
    now = datetime(2026, 6, 18, 12, 0, 0)
    porucheniya = [
        {
            "document_number": "АСТ00-00039",
            "document_date": "2026-06-17T15:12:24",
            "status": "ВРаботе",
            "tasks": [
                {
                    "activity": "Критическая задача",
                    "due_date": "2026-06-20T00:00:00",
                    "has_file": "Нет",
                    "priority": "Критический",
                }
            ],
        }
    ]
    summary_note = "Загружено: поручений 1 (1 мероприятий), протоколов 0 (0 задач)"

    metrics = build_tasks_metrics(
        porucheniya,
        [],
        summary_note=summary_note,
        report_day=now.date(),
        now=now,
    )

    by_key = {row["key"]: row for row in metrics["rows"]}
    assert by_key["total_under_control"]["count"] == 1
    assert by_key["total_under_control"]["note"] == summary_note
    assert by_key["new_tasks_today"]["count"] == 0
    assert by_key["due_today"]["count"] == 0
    assert by_key["due_in_1_3_business_days"]["count"] == 1
    assert by_key["overdue_tasks"]["count"] == 0
    assert by_key["critical_overdues"]["count"] == 1
    assert by_key["completed_without_artifact"]["count"] == 0


def test_build_tasks_metrics_counts_overdue_and_completed_flags() -> None:
    now = datetime(2026, 6, 18, 12, 0, 0)
    protocols = [
        {
            "document_date": "2026-06-18T10:00:00",
            "status": "ВРаботе",
            "tasks": [
                {
                    "activity": "Просроченная",
                    "assigned_date": "2026-06-18T10:00:00",
                    "due_date": "2026-06-10T00:00:00",
                    "completed": False,
                    "confirmed": False,
                    "has_file": "Нет",
                    "priority": "Высокий",
                },
                {
                    "activity": "Закрыта без файла",
                    "completed": True,
                    "confirmed": False,
                    "completed_date": "2026-06-18T00:00:00",
                    "has_file": "Нет",
                    "priority": "Средний",
                    "note": "Запрошен перенос срока без основания",
                },
            ],
        }
    ]

    metrics = build_tasks_metrics([], protocols, summary_note="", report_day=now.date(), now=now)
    counts = metrics["counts"]

    assert counts["new_tasks_today"] == 2
    assert counts["overdue_tasks"] == 1
    assert counts["completed_without_artifact"] == 1
    assert counts["completed_without_confirmation"] == 1
    assert counts["postponement_requested"] == 1
    assert counts["postponement_without_basis"] == 1
    assert counts["closed_today"] == 1
