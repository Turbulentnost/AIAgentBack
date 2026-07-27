"""Оффлайн-справочник должностей из enterprise_positions_report_all.txt (пока 1С недоступен)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.tools.onec.lookup_user_ref import normalize_name

DEFAULT_REPORT_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "onec" / "enterprise_positions_report_all.txt"
)
ORG_STRUCTURE_PREFIX = "Оргструктура:"
EMPLOYEE_LINE_RE = re.compile(
    r"^\s{4}-\s+(?P<name>.+?)(?:\s+с\s+(?P<since>\d{4}-\d{2}-\d{2}))?\s*$"
)

_DIRECTOR_POSITION_EXCLUDE_MARKERS = (
    "помощник председателя совета директоров",
)

_DIRECTOR_POSITION_MARKERS = (
    "директор по ",
    "заместитель директора",
    "зам. директора",
    "заместитель технического директора",
    "заместитель коммерческого директор",
    "заместитель операционного директора",
    "председатель совета директоров",
    "финансовый директор",
    "технический директор",
    "операционный директор",
    "коммерческий директор",
    "управляющий директор",
    "генеральный директор",
    "исполнительный директор",
    "заместитель генерального директора",
)


@dataclass(frozen=True, slots=True)
class EnterprisePositionAssignment:
    fio: str
    position: str
    department: str
    since: str | None = None


def resolve_report_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    env_path = os.getenv("MEETING_ENTERPRISE_POSITIONS_REPORT")
    if env_path:
        return Path(env_path)
    return DEFAULT_REPORT_PATH


def normalize_position_title(value: str | None) -> str:
    text = (value or "").strip().casefold().replace("ё", "е")
    return " ".join(text.split())


def is_director_position_title(position: str | None) -> bool:
    normalized = normalize_position_title(position)
    if not normalized:
        return False
    if any(marker in normalized for marker in _DIRECTOR_POSITION_EXCLUDE_MARKERS):
        return False
    if normalized == "директор":
        return True
    return any(marker in normalized for marker in _DIRECTOR_POSITION_MARKERS)


def parse_enterprise_positions_report(text: str) -> list[EnterprisePositionAssignment]:
    assignments: list[EnterprisePositionAssignment] = []
    current_department = ""
    current_position = ""

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("Найдено назначений:"):
            continue

        employee_match = EMPLOYEE_LINE_RE.match(line)
        if employee_match:
            if not current_position:
                continue
            assignments.append(
                EnterprisePositionAssignment(
                    fio=employee_match.group("name").strip(),
                    position=current_position,
                    department=current_department,
                    since=employee_match.group("since"),
                )
            )
            continue

        if line.startswith("    "):
            continue

        if line.startswith("  ") and not line.startswith("   "):
            current_position = line[2:].strip()
            continue

        if line.startswith(ORG_STRUCTURE_PREFIX):
            current_department = line[len(ORG_STRUCTURE_PREFIX) :].strip()
        else:
            current_department = line.strip()
        current_position = ""

    return assignments


def build_fio_index(
    assignments: list[EnterprisePositionAssignment],
) -> dict[str, list[EnterprisePositionAssignment]]:
    index: dict[str, list[EnterprisePositionAssignment]] = {}
    for item in assignments:
        key = normalize_name(item.fio)
        if not key:
            continue
        bucket = index.setdefault(key, [])
        if item not in bucket:
            bucket.append(item)
    return index


@lru_cache(maxsize=1)
def _load_index(report_path: str) -> dict[str, list[EnterprisePositionAssignment]]:
    path = Path(report_path)
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    return build_fio_index(parse_enterprise_positions_report(text))


def lookup_assignments_by_fio(
    fio: str,
    *,
    report_path: str | Path | None = None,
) -> list[EnterprisePositionAssignment]:
    key = normalize_name(fio)
    if not key:
        return []
    index = _load_index(str(resolve_report_path(report_path)))
    return list(index.get(key, []))


def build_position_title_index(
    assignments: list[EnterprisePositionAssignment],
) -> dict[str, list[EnterprisePositionAssignment]]:
    index: dict[str, list[EnterprisePositionAssignment]] = {}
    for item in assignments:
        key = normalize_position_title(item.position)
        if not key:
            continue
        bucket = index.setdefault(key, [])
        if item not in bucket:
            bucket.append(item)
    return index


@lru_cache(maxsize=1)
def _load_position_title_index(report_path: str) -> dict[str, list[EnterprisePositionAssignment]]:
    path = Path(report_path)
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    return build_position_title_index(parse_enterprise_positions_report(text))


def lookup_fios_by_position_title(
    position: str,
    *,
    report_path: str | Path | None = None,
) -> list[str]:
    key = normalize_position_title(position)
    if not key:
        return []
    index = _load_position_title_index(str(resolve_report_path(report_path)))
    assignments = index.get(key, [])
    fios: list[str] = []
    seen: set[str] = set()
    for item in assignments:
        normalized_fio = normalize_name(item.fio)
        if not normalized_fio or normalized_fio in seen:
            continue
        seen.add(normalized_fio)
        fios.append(item.fio.strip())
    return fios


def lookup_positions_by_fio(
    fio: str,
    *,
    report_path: str | Path | None = None,
) -> list[str]:
    positions: list[str] = []
    seen: set[str] = set()
    for item in lookup_assignments_by_fio(fio, report_path=report_path):
        title = item.position.strip()
        if not title:
            continue
        normalized = normalize_position_title(title)
        if normalized in seen:
            continue
        seen.add(normalized)
        positions.append(title)
    return positions


def primary_position_for_fio(
    fio: str,
    *,
    report_path: str | Path | None = None,
) -> str | None:
    positions = lookup_positions_by_fio(fio, report_path=report_path)
    if not positions:
        return None
    for position in positions:
        if is_director_position_title(position):
            return position
    return positions[0]


def is_director_by_fio(
    fio: str,
    *,
    report_path: str | Path | None = None,
) -> bool:
    return any(
        is_director_position_title(position)
        for position in lookup_positions_by_fio(fio, report_path=report_path)
    )


def person_fio(person: Any) -> str | None:
    if not isinstance(person, dict):
        return None
    for key in ("full_name", "fio", "ФИО", "Description"):
        value = person.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def enrich_person_from_positions_report(
    person: Any,
    *,
    report_path: str | Path | None = None,
) -> dict[str, Any] | None:
    if not isinstance(person, dict):
        return None
    fio = person_fio(person)
    if not fio:
        return dict(person)

    enriched = dict(person)
    if not enriched.get("position") and not enriched.get("Должность"):
        primary = primary_position_for_fio(fio, report_path=report_path)
        if primary:
            enriched["position"] = primary

    if is_director_by_fio(fio, report_path=report_path):
        enriched["is_director"] = True
    return enriched
