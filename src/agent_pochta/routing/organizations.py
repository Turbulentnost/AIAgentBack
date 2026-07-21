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

# КС — только для неясных/резервных писем; явные запросы, коммерция и обращения → ПР.
DIRECTION_UNCLEAR = "КС"
DIRECTION_DEFAULT = "ПР"
DIRECTION_COMMERCIAL = "ПР"

# Отделы коммерческого контура: направление всегда ПР, даже при устаревших правилах с КС.
COMMERCIAL_DEPARTMENT_CODES = frozenset(
    {
        "00-000015",  # ВЭД
        "00-000042",  # ОРКК
        "00-000054",  # тендеры
        "00-000058",  # коммерческий директор
        "00-000076",  # ОПГ / Gazprom
        "00-000155",  # ОДП
    }
)


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
        return DIRECTION_DEFAULT
    return existing or DIRECTION_DEFAULT


def resolve_direction_for_department(
    department_code: str,
    organization: str,
    *,
    rules: dict | None = None,
    fallback_direction: str | None = None,
) -> str:
    """Направление XML по коду отдела (RAG/LLM/HITL), с приоритетом коммерческого контура."""
    if organization in _ORG_DIRECTION_CODES:
        return organization

    code = (department_code or "").strip()

    if code in COMMERCIAL_DEPARTMENT_CODES:
        return DIRECTION_COMMERCIAL

    candidate = (fallback_direction or "").strip() or None
    if rules and code:
        from agent_pochta.services.routing_departments import directions_by_code_from_rules

        rule_direction = (directions_by_code_from_rules(rules).get(code) or "").strip()
        if rule_direction:
            candidate = rule_direction

    if candidate:
        return candidate
    if code:
        return DIRECTION_DEFAULT
    return DIRECTION_UNCLEAR
