from datetime import datetime

from app.agents.tasks_agent.table_presenter import build_porucheniya_tasks_table, build_tasks_table


def test_build_porucheniya_tasks_table_matches_template_columns() -> None:
    now = datetime(2026, 6, 18, 12, 0, 0)
    porucheniya = [
        {
            "document_ref": "doc-1",
            "document_number": "АСТ00-00039",
            "document_date": "2026-05-29T15:12:24",
            "status": "ВРаботе",
            "manager": "Амураль Игорь Борисович",
            "reviewer": "Ильченко Екатерина Александровна",
            "tasks": [
                {
                    "line_number": "1",
                    "activity": "Обеспечить проведение совета директоров",
                    "responsible": "Ростовцева Анастасия Вадимовна",
                    "department": "Департамент цифровизации / Управление проектами",
                    "due_date": "2026-06-02T00:00:00",
                    "has_file": "Нет",
                    "priority": "Высокий",
                }
            ],
        }
    ]

    table = build_porucheniya_tasks_table(porucheniya, now=now)
    assert table["row_count"] == 1
    assert len(table["columns"]) == 15
    row = table["rows"][0]
    assert row["document_number"] == "АСТ00-00039"
    assert row["document_date"] == "29.05.2026"
    assert row["task_text"] == "Обеспечить проведение совета директоров"
    assert row["assignee"] == "Ростовцева Анастасия Вадимовна"
    assert row["reviewer"] == "Ильченко Екатерина Александровна"
    assert row["department"] == "Управление проектами"
    assert row["due_date"] == "02.06.2026"
    assert row["status"] == "Просрочено"
    assert row["overdue_days"] == 16
    assert row["artifact"] == "Нет"


def test_build_tasks_table_includes_protocols() -> None:
    now = datetime(2026, 6, 18, 12, 0, 0)
    protocols = [
        {
            "document_number": "ПСД_001_О_102",
            "document_date": "2026-05-20T10:00:00",
            "status": "ВРаботе",
            "reviewer": "Ильченко Екатерина Александровна",
            "tasks": [
                {
                    "activity": "Подготовить материалы",
                    "responsible": "Исполнитель 1",
                    "department": "Департамент цифровизации",
                    "due_date": "2026-06-10T00:00:00",
                    "has_file": "Нет",
                }
            ],
        }
    ]

    table = build_tasks_table([], protocols, now=now)
    assert table["row_count"] == 1
    assert table["rows"][0]["document_number"] == "ПСД_001_О_102"
    assert table["rows"][0]["task_text"] == "Подготовить материалы"
    assert table["rows"][0]["reviewer"] == "Ильченко Екатерина Александровна"
