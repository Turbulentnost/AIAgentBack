from __future__ import annotations

import re

# Базовый допустимый набор символов обозначения.
ESKD_DESIGNATION_CHARS_RE = re.compile(
    r"^[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9.\-/]{2,127}$",
    re.IGNORECASE,
)

# ГОСТ 2.201 (упрощённо): код организации . порядковый номер [. номер листа][суффикс].
ESKD_STANDARD_DESIGNATION_RE = re.compile(
    r"^(?P<org>[A-ZА-ЯЁ]{1,9})\.(?P<serial>\d{2,6})(?:\.(?P<sheet>\d{1,3}))?"
    r"(?P<suffix>(?:СБ|Э[1-9]|В[1-9]|Д[1-9]|О[1-9]|М[1-9]|Ч[1-9])?)$",
    re.IGNORECASE,
)

ASSEMBLY_SUFFIX_RE = re.compile(r"\.?СБ$", re.IGNORECASE)
ELECTRIC_SUFFIX_RE = re.compile(r"Э[1-9]$", re.IGNORECASE)


def normalize_designation(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    return value.strip().upper()


def parse_designation(value: str | None) -> dict[str, str | None] | None:
    normalized = normalize_designation(value)
    if not normalized:
        return None
    match = ESKD_STANDARD_DESIGNATION_RE.match(normalized)
    if not match:
        return None
    return {
        "org": match.group("org").upper(),
        "serial": match.group("serial"),
        "sheet": match.group("sheet"),
        "suffix": (match.group("suffix") or "").upper() or None,
        "full": normalized,
    }
