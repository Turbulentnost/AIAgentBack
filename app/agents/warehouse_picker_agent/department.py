from __future__ import annotations

import re

# Заказы материалов с этими подразделениями идут кладовщику-комплектовщику,
# а не инженеру по подготовке производства.
_MONTAGE_SECTION_2_PATTERNS = (
    re.compile(r"монтажн\w*\s+участ\w*.*(?:№\s*)?2\b", re.IGNORECASE),
    re.compile(r"механическ\w*\s+участ\w*.*(?:№\s*)?2\b", re.IGNORECASE),
)


def normalize_department_name(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.casefold().replace("ё", "е").split())


def is_montage_section_2_department(department_name: str | None) -> bool:
    """True for «Монтажный участок №2» / «Механический участок 2»."""
    normalized = normalize_department_name(department_name)
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _MONTAGE_SECTION_2_PATTERNS)


__all__ = ["is_montage_section_2_department", "normalize_department_name"]
