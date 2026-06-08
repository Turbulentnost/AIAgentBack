from __future__ import annotations


def is_liquidated_department_name(name: str | None) -> bool:
    if not name:
        return False
    lowered = name.lower()
    return "ликвид" in lowered or "(ликв" in lowered
