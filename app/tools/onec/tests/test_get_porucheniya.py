from datetime import datetime

from app.tools.onec.get_porucheniya import compute_priority, parse_input_date


def test_parse_input_date_accepts_iso_string() -> None:
    assert parse_input_date("2026-03-01") == parse_input_date("2026-03-01T12:00:00")


def test_compute_priority_marks_due_today_as_high() -> None:
    now = datetime(2026, 6, 18, 15, 0, 0)
    due = datetime(2026, 6, 18, 0, 0, 0)
    assert (
        compute_priority(
            due_date=due,
            confirmed=False,
            completed=False,
            has_file=True,
            manager="",
            now=now,
        )
        == "Высокий"
    )


def test_compute_priority_overdue_without_file_becomes_high() -> None:
    now = datetime(2026, 6, 18, 15, 0, 0)
    due = datetime(2026, 6, 10, 0, 0, 0)
    assert (
        compute_priority(
            due_date=due,
            confirmed=False,
            completed=True,
            has_file=False,
            manager="",
            now=now,
        )
        == "Высокий"
    )


def test_compute_priority_critical_manager() -> None:
    now = datetime(2026, 6, 18, 15, 0, 0)
    due = datetime(2026, 7, 1, 0, 0, 0)
    assert (
        compute_priority(
            due_date=due,
            confirmed=False,
            completed=False,
            has_file=True,
            manager="Амураль Игорь Борисович",
            now=now,
        )
        == "Критический"
    )
