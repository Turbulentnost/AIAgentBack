from __future__ import annotations

from app.tools.TurboProject.working_group import (
    extract_working_group_members,
    normalize_person_name,
)


def test_normalize_person_name_rejects_none_string() -> None:
    assert normalize_person_name("None") is None
    assert normalize_person_name("null") is None
    assert normalize_person_name("Соломичева Светлана Викторовна") == (
        "Соломичева Светлана Викторовна"
    )


def test_extract_uses_project_manager_when_data_1c_empty() -> None:
    members = extract_working_group_members(
        {
            "data_1c": None,
            "manager": "Соломичева Светлана Викторовна",
            "curator": "None",
            "author": "Соломичева Светлана Викторовна",
            "resources": [
                "Комарькова Анастасия Эдуардовна",
                "Соломичева Светлана Викторовна",
            ],
        }
    )
    by_role = {item["role"]: item["fio"] for item in members}
    assert by_role["Руководитель проекта"] == "Соломичева Светлана Викторовна"
    assert "Куратор" not in by_role
    assert by_role["Ресурс проекта"] == "Комарькова Анастасия Эдуардовна"
    assert sum(1 for item in members if item["fio"].startswith("Соломичева")) == 1


def test_extract_prefers_data_1c_rukovoditel() -> None:
    members = extract_working_group_members(
        {
            "data_1c": {"rukovoditel": "Иванов Иван Иванович", "kurator": "Петров Пётр"},
            "manager": "Соломичева Светлана Викторовна",
            "resources": ["Сидоров"],
        }
    )
    by_role = {item["role"]: item["fio"] for item in members}
    assert by_role["Руководитель проекта"] == "Иванов Иван Иванович"
    assert by_role["Куратор"] == "Петров Пётр"
