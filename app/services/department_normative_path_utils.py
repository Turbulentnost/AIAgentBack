from __future__ import annotations

from dataclasses import dataclass

from app.services import list_enterprise_positions as onec
from app.services.onec_departments_fetcher import EnterpriseDepartment

DEPARTMENTS_ROOT_SEGMENT = "нормативные документы по подразделениям"
EXCLUDED_FOLDER_SEGMENTS = frozenset({"архив", "общее"})


def normalize_segment(value: str) -> str:
    return onec.normalize_text(value)


NON_DEPARTMENT_ROOT_SEGMENTS = frozenset(
    {
        normalize_segment("Документы по сварке"),
    }
)


@dataclass(frozen=True)
class ParsedNormativePath:
    folder_department: str | None
    scope_parts: tuple[str, ...]
    excluded_reason: str | None
    relative_path: str


def parse_normative_relative_path(relative_path: str | None) -> ParsedNormativePath:
    raw = (relative_path or "").replace("\\", "/").strip()
    parts = [segment.strip() for segment in raw.split("/") if segment.strip()]
    normalized = [normalize_segment(part) for part in parts]

    if not parts:
        return ParsedNormativePath(
            folder_department=None,
            scope_parts=tuple(),
            excluded_reason=None,
            relative_path=raw,
        )

    if normalized[0] in EXCLUDED_FOLDER_SEGMENTS:
        return ParsedNormativePath(
            folder_department=None,
            scope_parts=tuple(),
            excluded_reason=parts[0],
            relative_path=raw,
        )

    try:
        root_index = normalized.index(DEPARTMENTS_ROOT_SEGMENT)
        department_index = root_index + 1
    except ValueError:
        if normalized[0] in NON_DEPARTMENT_ROOT_SEGMENTS:
            return ParsedNormativePath(
                folder_department=None,
                scope_parts=tuple(),
                excluded_reason=None,
                relative_path=raw,
            )
        department_index = 0

    if department_index >= len(parts):
        return ParsedNormativePath(
            folder_department=None,
            scope_parts=tuple(),
            excluded_reason=None,
            relative_path=raw,
        )

    for segment, norm in zip(parts[department_index + 1 :], normalized[department_index + 1 :], strict=False):
        if norm in EXCLUDED_FOLDER_SEGMENTS:
            return ParsedNormativePath(
                folder_department=None,
                scope_parts=tuple(),
                excluded_reason=segment,
                relative_path=raw,
            )

    folder_department = parts[department_index]
    scope_parts = tuple(parts[department_index + 1 : -1]) if len(parts) > department_index + 1 else tuple()
    return ParsedNormativePath(
        folder_department=folder_department,
        scope_parts=scope_parts,
        excluded_reason=None,
        relative_path=raw,
    )


def match_enterprise_department(
    folder_department: str,
    departments: list[EnterpriseDepartment],
) -> tuple[EnterpriseDepartment | None, str | None]:
    target = normalize_segment(folder_department)
    if not target:
        return None, "Пустое имя подразделения в пути"

    exact = [item for item in departments if normalize_segment(item.name) == target]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, f"Неоднозначное совпадение с 1С: {folder_department}"

    partial = [
        item
        for item in departments
        if target in normalize_segment(item.name) or normalize_segment(item.name) in target
    ]
    if len(partial) == 1:
        return partial[0], None
    if len(partial) > 1:
        return None, f"Несколько кандидатов в 1С для «{folder_department}»"

    return None, f"Подразделение «{folder_department}» не найдено в 1С"


def related_departments_from_path(department: EnterpriseDepartment) -> list[str]:
    parts = [part.strip() for part in department.path.split("/") if part.strip()]
    if len(parts) <= 1:
        return []
    return parts[:-1]
