"""Коды организаций ТЗ и справочник для UI/API."""

from __future__ import annotations

ORG_FULL_NAMES: dict[str, str] = {
    "НП": "НПО «Турбулентность-ДОН»",
    "АЛ": "ООО «Алмаз»",
    "МГ": "ООО «Метрогазсервис»",
    "АМ": "ООО «Амурская легенда»",
    "МИ": "ООО «МИЛАКА»",
    "БМ": "БМИ (блочно-модульные изделия)",
}

ORG_ORDER = ("НП", "АЛ", "МГ", "АМ", "МИ", "БМ")

_ORG_DIRECTION_CODES = frozenset({"АЛ", "МГ", "АМ", "МИ", "БМ"})


def list_organizations_for_ui() -> list[dict[str, str]]:
    """Список организаций для выпадающего списка HITL."""
    return [{"id": code, "name": ORG_FULL_NAMES[code]} for code in ORG_ORDER]


def normalize_organization_code(value: str | None) -> str | None:
    """Проверяет и нормализует код организации."""
    if value is None:
        return None
    code = str(value).strip()
    if not code:
        return None
    if code in ORG_FULL_NAMES:
        return code
    return None


def direction_for_organization_override(
    organization: str,
    *,
    existing_direction: str | None = None,
    previous_organization: str | None = None,
) -> str:
    """Направление XML при смене организации оператором (RuleRouter.detect_direction + сброс org-кода)."""
    if organization in _ORG_DIRECTION_CODES:
        return organization

    existing = (existing_direction or "").strip()
    previous = (previous_organization or "").strip()
    if previous in _ORG_DIRECTION_CODES and existing == previous:
        return "КС"
    return existing or "КС"
