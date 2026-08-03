"""
Рабочая группа проекта TurboProject для серии совещаний.

Собирает ФИО из ресурсов MS Project и ролей проекта из 1С.

Пример:
  python -m app.tools.TurboProject.working_group --file-id 12
  python -m app.tools.TurboProject.working_group --project-name Turbo
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.tools.TurboProject.connection import TurboProjectError
from app.tools.TurboProject.projects import (
    get_turbo_project,
    unique_resource_names,
)


WORKING_GROUP_1C_ROLES: tuple[tuple[str, str], ...] = (
    ("rukovoditel", "Руководитель проекта"),
    ("kurator", "Куратор"),
    ("zakazchik", "Заказчик"),
    ("investor", "Инвестор"),
    ("zam_rp", "Заместитель РП"),
)


def normalize_person_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized or normalized.casefold() in {"none", "null", "nil", "-"}:
            return None
        return normalized
    if isinstance(value, dict):
        for key in ("Description", "description", "name", "Name", "fio", "FIO"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return normalize_person_name(candidate)
    if isinstance(value, list):
        parts = [normalize_person_name(item) for item in value]
        cleaned = [part for part in parts if part]
        if cleaned:
            return ", ".join(cleaned)
    return None


def extract_working_group_members(project: dict[str, Any]) -> list[dict[str, str]]:
    members: list[dict[str, str]] = []
    seen: set[str] = set()

    def append_member(*, fio: str, role: str, source: str) -> None:
        normalized = fio.strip()
        if not normalized:
            return
        key = normalized.casefold()
        if key in seen:
            return
        seen.add(key)
        members.append({"fio": normalized, "role": role, "source": source})

    def has_role(role_label: str) -> bool:
        return any(member.get("role") == role_label for member in members)

    data_1c = project.get("data_1c") if isinstance(project.get("data_1c"), dict) else {}
    for field, role_label in WORKING_GROUP_1C_ROLES:
        fio = normalize_person_name(data_1c.get(field))
        if fio:
            append_member(fio=fio, role=role_label, source="1c")

    # В UI TurboProject РП/куратор часто в project.manager / project.curator, а не в data_1c.
    if not has_role("Руководитель проекта"):
        manager_fio = (
            normalize_person_name(project.get("manager"))
            or normalize_person_name(project.get("project_manager_display"))
            or normalize_person_name(project.get("author"))
        )
        if manager_fio:
            append_member(fio=manager_fio, role="Руководитель проекта", source="msp")

    if not has_role("Куратор"):
        curator_fio = normalize_person_name(project.get("curator"))
        if curator_fio:
            append_member(fio=curator_fio, role="Куратор", source="msp")

    for resource in project.get("resources") or []:
        if isinstance(resource, str):
            append_member(fio=resource, role="Ресурс проекта", source="msp")

    return members


def build_working_group_payload(project: dict[str, Any]) -> dict[str, Any]:
    members = extract_working_group_members(project)
    return {
        "file_id": project.get("file_id"),
        "project_name": project.get("project_name"),
        "one_c_ref_key": (
            (project.get("data_1c") or {}).get("one_c_ref_key")
            if isinstance(project.get("data_1c"), dict)
            else None
        ),
        "members_count": len(members),
        "member_fios": [member["fio"] for member in members],
        "members": members,
        "resources": unique_resource_names(list(project.get("resources") or [])),
    }


def get_turbo_project_working_group(
    *,
    file_id: int | None = None,
    project_name: str | None = None,
    one_c_ref_key: str | None = None,
) -> dict[str, Any]:
    project = get_turbo_project(
        file_id=file_id,
        project_name=project_name,
        one_c_ref_key=one_c_ref_key,
    )
    payload = build_working_group_payload(project)
    payload["project"] = {
        "file_id": project.get("file_id"),
        "project_name": project.get("project_name"),
        "dates": project.get("dates"),
        "task_stats": project.get("task_stats"),
        "data_1c": project.get("data_1c"),
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TurboProject: рабочая группа проекта")
    parser.add_argument("--file-id", type=int, default=None)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--one-c-ref-key", default=None)
    args = parser.parse_args(argv)

    try:
        payload = get_turbo_project_working_group(
            file_id=args.file_id,
            project_name=args.project_name,
            one_c_ref_key=args.one_c_ref_key,
        )
    except (TurboProjectError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
