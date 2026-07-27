"""Справочник 8 ГОСТ для compact-формата."""

from __future__ import annotations

import re

GOST_LINE_ORDER: list[tuple[str, str]] = [
    ("2.104", "ГОСТ Р 2.104-2023 — штамп, подписи, лист/листов"),
    ("2.201", "ГОСТ Р 2.201-2023 — обозначение"),
    ("2.105", "ГОСТ 2.105 — спецификация / BOM"),
    ("2.109", "ГОСТ Р 2.109-2023 — чертежи, виды, выноски"),
    ("2.503", "ГОСТ Р 2.503-2023 — изменения"),
    ("2.316", "ГОСТ Р 2.316-2023 — ТТ и надписи"),
    ("2.308", "ГОСТ Р 2.308-2023 — допуски"),
    ("2.301", "ГОСТ 2.301 — форматы, масштаб"),
]

GOST_LINE_KEYS: list[str] = [key for key, _ in GOST_LINE_ORDER]

# Cross-page package errors без явного списка pages — применяются ко всем листам items.
DOCUMENT_WIDE_PACKAGE_CODES = frozenset({"sheet_sequence", "designation_mismatch_across_pages"})

ERROR_CODE_TO_LINE: dict[str, str] = {
    "missing_signature": "2.104",
    "sheet_mismatch": "2.104",
    "title_mismatch": "2.104",
    "designation_mismatch": "2.201",
    "typo_in_designation": "2.201",
    "position_order_mismatch": "2.105",
    "position_missing_in_bom": "2.105",
    "foreign_overlay": "2.105",
    "field_unreadable": "2.104",
    "sheet_sequence": "2.104",
    "designation_mismatch_across_pages": "2.201",
    "revision_mismatch": "2.503",
}

ELEMENT_TO_LINE: dict[str, str] = {
    "designation": "2.201",
    "title": "2.104",
    "sheet": "2.104",
    "sheets_total": "2.104",
    "scale": "2.301",
    "revision": "2.503",
    "specification": "2.105",
    "views": "2.109",
    "references": "2.316",
    "dimensions": "2.308",
}

ZONE_TO_LINE: dict[str, str] = {
    "title_block": "2.104",
    "specification": "2.105",
    "drawing": "2.109",
    "views": "2.109",
    "revision": "2.503",
    "references": "2.316",
    "dimensions": "2.308",
    "scale": "2.301",
}

_GOST_REF_RE = re.compile(r"2\.\d{3}")


def gost_reference_to_line(gost_reference: str | None) -> str | None:
    if not gost_reference:
        return None
    match = _GOST_REF_RE.search(str(gost_reference))
    if not match:
        return None
    key = match.group(0)
    return key if key in GOST_LINE_KEYS else None


def issue_to_line(
    *,
    code: str | None,
    element: str | None,
    zone: str | None,
    gost_reference: str | None,
) -> str | None:
    if code:
        mapped = ERROR_CODE_TO_LINE.get(code)
        if mapped:
            if code == "field_unreadable" and zone in ZONE_TO_LINE:
                return ZONE_TO_LINE[zone]
            return mapped
    ref_line = gost_reference_to_line(gost_reference)
    if ref_line:
        return ref_line
    if element:
        return ELEMENT_TO_LINE.get(element) or ZONE_TO_LINE.get(str(zone or ""))
    if zone:
        return ZONE_TO_LINE.get(zone)
    return None


def gost_catalog() -> list[dict[str, str]]:
    return [{"key": key, "title": title} for key, title in GOST_LINE_ORDER]
