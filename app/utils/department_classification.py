from __future__ import annotations

import re

from app.utils.department_utils import is_liquidated_department_name

_ORG_UNIT_MARKERS = (
    "отдел",
    "служба",
    "управление",
    "бюро",
    "цех",
    "участок",
    "группа",
    "лаборатор",
    "подраздел",
    "дирекция",
    "департамент",
    "сектор",
    "филиал",
    "представитель",
    "склад",
    "комплекс",
    "центр",
    "бухгалтерия",
    "производство",
    "монтаж",
    "заготов",
    "изготовлен",
    "техническое бюро",
    "конструкторск",
)

_POSITION_TITLE_PATTERNS = (
    r"^зам\.?\s",
    r"^зам\s",
    r"^заместитель\b",
    r"^помощник\b",
    r"^специалист\b",
    r"^электрик",
    r"^председатель\b",
    r"^главный\s+конструктор\b",
    r"^главный\s+метролог\b",
    r"^(коммерческий|операционный|технический|финансовый|исполнительный)\s+директор\b",
    r"^директор\b",
    r"^начальник\b",
    r"^руководитель\b",
    r"^зам\.?\s*коммерческого\s+директора\b",
    r"^зам\.?\s*технического\s+директора\b",
    r"^зам\.?\s*операционного\s+директора\b",
    r"^зам\.?\s*директора\b",
)


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower().replace("ё", "е")).strip()


def _has_org_unit_marker(name: str) -> bool:
    normalized = _normalized(name)
    return any(marker in normalized for marker in _ORG_UNIT_MARKERS)


def is_position_like_department_name(name: str | None) -> bool:
    """True when a 1C structure node name is a job title, not an org unit."""
    if not name or is_liquidated_department_name(name):
        return False

    normalized = _normalized(name)
    if not normalized:
        return False

    if _has_org_unit_marker(name):
        return False

    return any(re.search(pattern, normalized) for pattern in _POSITION_TITLE_PATTERNS)


def is_schedule_participant_department_name(name: str | None) -> bool:
    """True when a department row can be used as a schedule participant role."""
    if not name or is_liquidated_department_name(name):
        return False

    normalized = _normalized(name)
    if not normalized:
        return False

    return not _has_org_unit_marker(name)


def normalize_position_name(name: str) -> str:
    """Expand common abbreviations from 1C structure nodes into readable titles."""
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    if not cleaned:
        return cleaned

    lowered = cleaned.lower()
    if lowered.startswith("зам."):
        cleaned = "Заместитель" + cleaned[4:]
    elif lowered.startswith("зам "):
        cleaned = "Заместитель" + cleaned[3:]

    return re.sub(r"\s+", " ", cleaned).strip()
