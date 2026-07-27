from unittest.mock import MagicMock, patch

import pytest

from app.tools.TurboProject.projects import (
    build_overdue_tasks,
    build_project_payload,
    build_project_resources,
    list_turbo_projects,
    resolve_project_file_id,
)
from app.tools.TurboProject.working_group import (
    build_working_group_payload,
    extract_working_group_members,
    normalize_person_name,
)


def test_build_project_resources_from_resources_list() -> None:
    details = {"resources": ["Alpha User", " Beta User", "Alpha User"]}
    assert build_project_resources(details) == ["Alpha User", "Beta User"]


def test_build_project_resources_from_assignments() -> None:
    details = {
        "tasks": [
            {
                "assignments": [
                    {"resource_name": "Сидоров С.С."},
                    {"resource_name": "Иванов И.И."},
                ]
            }
        ]
    }
    assert build_project_resources(details) == ["Иванов И.И.", "Сидоров С.С."]


def test_build_overdue_tasks_skips_completed_and_summary() -> None:
    tasks = [
        {
            "is_summary": True,
            "finish_date": "2020-01-01T00:00:00",
            "percent_complete": 0.0,
        },
        {
            "finish_date": "2020-01-01T00:00:00",
            "percent_complete": 1.0,
            "name": "Done",
        },
        {
            "finish_date": "2020-01-01T00:00:00",
            "percent_complete": 0.5,
            "name": "Late",
            "assignments": [{"resource_name": "Иванов И.И."}],
        },
    ]
    overdue = build_overdue_tasks(tasks)
    assert len(overdue) == 1
    assert overdue[0]["name"] == "Late"
    assert overdue[0]["executors"] == ["Иванов И.И."]


def test_build_project_payload() -> None:
    summary = {"id": 7, "original_name": "demo.mpp", "uploaded_at": "2026-01-01T10:00:00", "has_1c": True}
    details = {
        "project": {"name": "Demo Project", "start_date": "2026-01-01T00:00:00"},
        "tasks": [{"is_summary": False, "percent_complete": 0.2, "finish_date": "2020-01-01T00:00:00"}],
        "resources": ["Иванов И.И."],
        "data_1c": {"nomer_proekta": "P-001"},
    }
    payload = build_project_payload(summary, details)
    assert payload["file_id"] == 7
    assert payload["project_name"] == "Demo Project"
    assert payload["resources"] == ["Иванов И.И."]
    assert payload["data_1c"]["nomer_proekta"] == "P-001"


def test_normalize_person_name_variants() -> None:
    assert normalize_person_name("  Иванов И.И. ") == "Иванов И.И."
    assert normalize_person_name({"Description": "Петров П.П."}) == "Петров П.П."
    assert normalize_person_name(["А", "Б"]) == "А, Б"


def test_extract_working_group_members_deduplicates() -> None:
    project = {
        "resources": ["Иванов И.И.", "Петров П.П."],
        "data_1c": {
            "rukovoditel": "Иванов И.И.",
            "kurator": "Сидоров С.С.",
        },
    }
    members = extract_working_group_members(project)
    assert [item["fio"] for item in members] == [
        "Иванов И.И.",
        "Сидоров С.С.",
        "Петров П.П.",
    ]
    assert members[0]["source"] == "1c"
    assert members[2]["source"] == "msp"


def test_build_working_group_payload() -> None:
    payload = build_working_group_payload(
        {
            "file_id": 3,
            "project_name": "Demo",
            "resources": ["Иванов И.И."],
            "data_1c": {"one_c_ref_key": "abc", "rukovoditel": "Иванов И.И."},
        }
    )
    assert payload["members_count"] == 1
    assert payload["member_fios"] == ["Иванов И.И."]
    assert payload["one_c_ref_key"] == "abc"


@patch("app.tools.TurboProject.projects.TurboProjectClient")
def test_list_turbo_projects_filters_by_query(mock_client_cls: MagicMock) -> None:
    client = MagicMock()
    mock_client_cls.return_value = client
    client.get.return_value = {
        "items": [
            {"id": 1, "original_name": "Alpha.mpp", "has_1c": True},
            {"id": 2, "original_name": "Beta.mpp", "has_1c": True},
        ]
    }

    result = list_turbo_projects(query="alpha", include_details=False, client=client)

    assert result["matched_count"] == 1
    assert result["projects"][0]["file_id"] == 1


@patch("app.tools.TurboProject.projects.fetch_project_details")
@patch("app.tools.TurboProject.projects.fetch_project_file_items")
def test_resolve_project_file_id_by_name(
    mock_fetch_items: MagicMock,
    mock_fetch_details: MagicMock,
) -> None:
    mock_fetch_items.return_value = [
        {"id": 10, "original_name": "Other.mpp", "has_1c": True},
        {"id": 11, "original_name": "Turbo.mpp", "has_1c": True},
    ]
    mock_fetch_details.side_effect = lambda file_id, client=None: {
        10: {"project": {"name": "Other Project"}},
        11: {"project": {"name": "Turbo Project"}},
    }[file_id]

    file_id = resolve_project_file_id(project_name="Turbo")

    assert file_id == 11


@patch("app.tools.TurboProject.projects.fetch_project_details")
@patch("app.tools.TurboProject.projects.fetch_project_file_items")
def test_resolve_project_file_id_ambiguous_raises(
    mock_fetch_items: MagicMock,
    mock_fetch_details: MagicMock,
) -> None:
    mock_fetch_items.return_value = [
        {"id": 10, "original_name": "Turbo A.mpp", "has_1c": False},
        {"id": 11, "original_name": "Turbo B.mpp", "has_1c": False},
    ]
    mock_fetch_details.return_value = {"project": {"name": "Turbo"}}

    with pytest.raises(ValueError, match="Найдено несколько проектов"):
        resolve_project_file_id(project_name="Turbo")
