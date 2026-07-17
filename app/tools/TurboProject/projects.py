"""
TurboProject: список проектов и детали MPP + 1С.

Примеры:
  python -m app.tools.TurboProject.projects
  python -m app.tools.TurboProject.projects --query Turbo --only-with-1c
  python -m app.tools.TurboProject.projects --file-id 12
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any

from app.tools.TurboProject.connection import TurboProjectClient, TurboProjectConfig, TurboProjectError


def parse_iso_date(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def unique_resource_names(names: list[Any]) -> list[str]:
    resources_by_key: dict[str, str] = {}
    for name in names:
        if not isinstance(name, str):
            continue
        normalized = name.strip()
        if not normalized:
            continue
        resources_by_key.setdefault(normalized.lower(), normalized)
    return sorted(resources_by_key.values(), key=str.lower)


def build_project_resources(details: dict[str, Any]) -> list[str]:
    resources = details.get("resources") or []
    if resources:
        return unique_resource_names(resources)

    assignment_resource_names: list[Any] = []
    for task in details.get("tasks") or []:
        for assignment in task.get("assignments") or []:
            assignment_resource_names.append(assignment.get("resource_name"))
    return unique_resource_names(assignment_resource_names)


def build_overdue_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today = datetime.now().date()
    overdue: list[dict[str, Any]] = []

    for task in tasks:
        if task.get("is_summary"):
            continue
        percent_complete = float(task.get("percent_complete") or 0.0)
        if percent_complete >= 1.0:
            continue
        finish_dt = parse_iso_date(task.get("finish_date"))
        if finish_dt is None or finish_dt.date() >= today:
            continue
        overdue.append(
            {
                "id": task.get("id"),
                "uid": task.get("uid"),
                "name": task.get("name"),
                "start_date": iso_or_none(task.get("start_date")),
                "finish_date": iso_or_none(task.get("finish_date")),
                "percent_complete": percent_complete,
                "executors": [
                    assignment.get("resource_name")
                    for assignment in (task.get("assignments") or [])
                    if assignment.get("resource_name")
                ],
            }
        )

    overdue.sort(key=lambda item: (item.get("finish_date") or "", item.get("name") or ""))
    return overdue


def build_overdue_milestones(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today = datetime.now().date()
    overdue: list[dict[str, Any]] = []

    for task in tasks:
        if not task.get("is_milestone"):
            continue
        percent_complete = float(task.get("percent_complete") or 0.0)
        if percent_complete >= 1.0:
            continue
        finish_dt = parse_iso_date(task.get("finish_date"))
        if finish_dt is None or finish_dt.date() >= today:
            continue
        overdue.append(
            {
                "id": task.get("id"),
                "uid": task.get("uid"),
                "name": task.get("name"),
                "start_date": iso_or_none(task.get("start_date")),
                "finish_date": iso_or_none(task.get("finish_date")),
                "percent_complete": percent_complete,
            }
        )

    overdue.sort(key=lambda item: (item.get("finish_date") or "", item.get("name") or ""))
    return overdue


def build_project_payload(summary_item: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
    project_meta = details.get("project") or {}
    tasks = details.get("tasks") or []
    overdue_tasks = build_overdue_tasks(tasks)
    overdue_milestones = build_overdue_milestones(tasks)
    resources = build_project_resources(details)

    non_summary_tasks = [task for task in tasks if not task.get("is_summary")]
    completed_tasks = [
        task for task in non_summary_tasks if float(task.get("percent_complete") or 0.0) >= 1.0
    ]

    return {
        "file_id": summary_item.get("id"),
        "original_name": summary_item.get("original_name"),
        "uploaded_at": iso_or_none(summary_item.get("uploaded_at")),
        "project_name": (project_meta or {}).get("name") or summary_item.get("original_name"),
        "has_1c": bool(summary_item.get("has_1c")),
        "dates": {
            "start_date": iso_or_none(project_meta.get("start_date")),
            "finish_date": iso_or_none(project_meta.get("finish_date")),
            "actual_finish_date": iso_or_none(project_meta.get("actual_finish_date")),
            "baseline_start": iso_or_none(project_meta.get("baseline_start")),
            "baseline_finish": iso_or_none(project_meta.get("baseline_finish")),
            "plan_finish_1c": iso_or_none(project_meta.get("plan_finish_1c")),
        },
        "task_stats": {
            "total_tasks": len(tasks),
            "non_summary_tasks": len(non_summary_tasks),
            "completed_tasks": len(completed_tasks),
            "overdue_tasks_count": len(overdue_tasks),
            "overdue_milestones_count": len(overdue_milestones),
        },
        "overdue_tasks": overdue_tasks,
        "overdue_milestones": overdue_milestones,
        "resources": resources,
        "data_1c": details.get("data_1c"),
    }


def build_project_summary(summary_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": summary_item.get("id"),
        "original_name": summary_item.get("original_name"),
        "uploaded_at": iso_or_none(summary_item.get("uploaded_at")),
        "project_name": summary_item.get("original_name"),
        "has_1c": bool(summary_item.get("has_1c")),
    }


def _normalize_query(value: str | None) -> str:
    return (value or "").strip().casefold()


def _matches_query(project_name: str | None, query: str | None) -> bool:
    normalized_query = _normalize_query(query)
    if not normalized_query:
        return True
    normalized_name = _normalize_query(project_name)
    return normalized_query in normalized_name


def fetch_project_file_items(
    client: TurboProjectClient | None = None,
) -> list[dict[str, Any]]:
    api_client = client or TurboProjectClient()
    summary = api_client.get("/api/projects/files")
    items = summary.get("items") or []
    if not isinstance(items, list):
        raise TurboProjectError("TurboProject /api/projects/files вернул неожиданный формат items")
    return [item for item in items if isinstance(item, dict)]


def fetch_project_details(
    file_id: int,
    *,
    client: TurboProjectClient | None = None,
) -> dict[str, Any]:
    api_client = client or TurboProjectClient()
    details = api_client.get(f"/api/projects/files/{file_id}")
    if not isinstance(details, dict):
        raise TurboProjectError(f"TurboProject project {file_id} вернул неожиданный формат")
    return details


def list_turbo_projects(
    *,
    only_with_1c: bool = True,
    query: str | None = None,
    include_details: bool = False,
    client: TurboProjectClient | None = None,
) -> dict[str, Any]:
    items = fetch_project_file_items(client=client)
    filtered = [item for item in items if not only_with_1c or item.get("has_1c")]

    projects: list[dict[str, Any]] = []
    for item in filtered:
        file_id = item.get("id")
        if not file_id:
            continue
        if include_details:
            details = fetch_project_details(int(file_id), client=client)
            project = build_project_payload(item, details)
        else:
            project = build_project_summary(item)
            project_meta = item.get("project") if isinstance(item.get("project"), dict) else {}
            if project_meta.get("name"):
                project["project_name"] = project_meta.get("name")
        if not _matches_query(str(project.get("project_name") or ""), query):
            continue
        projects.append(project)

    return {
        "total_projects": len(items),
        "projects_with_1c_count": sum(1 for item in items if item.get("has_1c")),
        "matched_count": len(projects),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "projects": projects,
    }


def get_turbo_project(
    *,
    file_id: int | None = None,
    project_name: str | None = None,
    one_c_ref_key: str | None = None,
    client: TurboProjectClient | None = None,
) -> dict[str, Any]:
    resolved_file_id = resolve_project_file_id(
        file_id=file_id,
        project_name=project_name,
        one_c_ref_key=one_c_ref_key,
        client=client,
    )
    items = fetch_project_file_items(client=client)
    summary_item = next((item for item in items if item.get("id") == resolved_file_id), None)
    if summary_item is None:
        summary_item = {"id": resolved_file_id, "has_1c": True}
    details = fetch_project_details(resolved_file_id, client=client)
    return build_project_payload(summary_item, details)


def resolve_project_file_id(
    *,
    file_id: int | None = None,
    project_name: str | None = None,
    one_c_ref_key: str | None = None,
    client: TurboProjectClient | None = None,
) -> int:
    if file_id is not None:
        return int(file_id)

    normalized_name = (project_name or "").strip()
    normalized_ref = (one_c_ref_key or "").strip()
    if not normalized_name and not normalized_ref:
        raise ValueError("Нужен file_id, project_name или one_c_ref_key")

    items = fetch_project_file_items(client=client)

    if normalized_ref:
        for item in items:
            if not item.get("has_1c"):
                continue
            current_id = item.get("id")
            if current_id is None:
                continue
            details = fetch_project_details(int(current_id), client=client)
            data_1c = details.get("data_1c") if isinstance(details.get("data_1c"), dict) else {}
            ref_key = str(data_1c.get("one_c_ref_key") or "").strip()
            if ref_key == normalized_ref:
                return int(current_id)
        raise ValueError(f"Проект TurboProject с one_c_ref_key={normalized_ref!r} не найден")

    matches: list[int] = []
    for item in items:
        current_id = item.get("id")
        if current_id is None:
            continue
        candidate_names = [str(item.get("original_name") or "")]
        if item.get("has_1c"):
            try:
                details = fetch_project_details(int(current_id), client=client)
            except TurboProjectError:
                continue
            project_meta = details.get("project") if isinstance(details.get("project"), dict) else {}
            if project_meta.get("name"):
                candidate_names.append(str(project_meta.get("name")))
        if any(_matches_query(name, normalized_name) for name in candidate_names):
            matches.append(int(current_id))

    if not matches:
        raise ValueError(f"Проект TurboProject {normalized_name!r} не найден")
    if len(matches) > 1:
        raise ValueError(
            "Найдено несколько проектов TurboProject по названию "
            f"{normalized_name!r}: {matches[:5]}"
        )
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TurboProject: список проектов и детали")
    parser.add_argument("--file-id", type=int, default=None, help="ID файла проекта")
    parser.add_argument("--query", default=None, help="Фильтр по названию проекта")
    parser.add_argument(
        "--only-with-1c",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Только проекты с синхронизацией 1С",
    )
    parser.add_argument(
        "--include-details",
        action="store_true",
        help="Загрузить полные детали для каждого проекта в списке",
    )
    args = parser.parse_args(argv)

    try:
        if args.file_id is not None:
            payload = get_turbo_project(file_id=args.file_id)
        else:
            payload = list_turbo_projects(
                only_with_1c=args.only_with_1c,
                query=args.query,
                include_details=args.include_details,
            )
    except (TurboProjectError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
