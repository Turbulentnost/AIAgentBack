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

# БМИ — направление внутри НПО; GUID организации 1С совпадает с НП.
ORGANIZATION_KEY_ALIASES: dict[str, str] = {
    "БМ": "НП",
}

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

# Производственный/снабженческий контур: направление ПР (не КС из legacy content-правил).
PRODUCTION_DIRECTION_DEPARTMENT_CODES = frozenset(
    {
        "00-000065",  # ОМТО
    }
)

# Руководство: направление плательщика всегда КС.
LEADERSHIP_DEPARTMENT_CODES = frozenset(
    {
        "00-000001",  # Председатель Совета Директоров
        "00-000152",  # Операционный директор
        "00-000182",  # Помощник зам. операционного директора
    }
)

# Юридический отдел: направление плательщика всегда КС.
LEGAL_DEPARTMENT_CODES = frozenset({"00-000044"})

KS_PAYER_DIRECTION_DEPARTMENT_CODES = LEADERSHIP_DEPARTMENT_CODES | LEGAL_DEPARTMENT_CODES

INFO_LEADERSHIP_MAILBOX = "info@turbo-don.ru"

# Явный адрес ящика директора (exact_email / email_keyword) — не content-эскалация.
_LEADERSHIP_DEDICATED_MAILBOX_SOURCES = frozenset({"exact_email", "email_keyword"})


def is_leadership_department(department_code: str | None) -> bool:
    code = (department_code or "").strip()
    return bool(code) and code in LEADERSHIP_DEPARTMENT_CODES


def is_info_leadership_mailbox(recipient: str, *, email_aliases: dict | None = None) -> bool:
    from agent_pochta.routing.normalize import normalize_email_address

    normalized = normalize_email_address(recipient, email_aliases)
    return normalized == INFO_LEADERSHIP_MAILBOX


def leadership_department_allowed(
    *,
    recipient: str,
    department_code: str,
    match_source: str,
    email_aliases: dict | None = None,
) -> bool:
    """Руководство (председатель/ОД/помощник) — только для info@ или явного ящика директора."""
    if not is_leadership_department(department_code):
        return True
    if is_info_leadership_mailbox(recipient, email_aliases=email_aliases):
        return True
    if match_source in _LEADERSHIP_DEDICATED_MAILBOX_SOURCES:
        return True
    if match_source == "human_correction":
        return True
    return False


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


def resolve_organization_key_code(code: str | None) -> str:
    """Код организации для lookup GUID в OData (БМ → НП)."""
    normalized = normalize_organization_code(code) or "НП"
    return ORGANIZATION_KEY_ALIASES.get(normalized, normalized)


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

    if code in KS_PAYER_DIRECTION_DEPARTMENT_CODES:
        return DIRECTION_UNCLEAR

    if code in COMMERCIAL_DEPARTMENT_CODES:
        return DIRECTION_COMMERCIAL

    if code in PRODUCTION_DIRECTION_DEPARTMENT_CODES:
        return DIRECTION_DEFAULT

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
