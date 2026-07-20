"""Канонические названия подразделений, отличающиеся от сокращений в 1С."""

DEPARTMENT_NAME_OVERRIDES: dict[str, str] = {}


def department_display_name(*, external_id: str | None, name: str) -> str:
    if external_id and external_id in DEPARTMENT_NAME_OVERRIDES:
        return DEPARTMENT_NAME_OVERRIDES[external_id]
    return name
