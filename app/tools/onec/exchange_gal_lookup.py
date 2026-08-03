"""Поиск корпоративного e-mail в глобальной адресной книге Exchange (EWS ResolveNames)."""
from __future__ import annotations

import re
from typing import Any

from exchangelib import Account

from app.core.config import settings
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.send_meeting_invite import connect_account, load_config
from app.tools.onec.lookup_user_ref import normalize_name

EXCHANGE_GAL_SOURCE = "Exchange-GAL(EWS ResolveNames)"
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_CYR_TO_LAT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def corporate_email_domain() -> str:
    return (settings.ONEC_CORPORATE_EMAIL_DOMAIN or "turbo-don.ru").strip().lower().lstrip("@")


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def is_valid_email(value: str) -> bool:
    email = normalize_email(value)
    return bool(email) and bool(EMAIL_PATTERN.match(email))


def is_corporate_email(email: str) -> bool:
    normalized = normalize_email(email)
    return bool(normalized) and normalized.endswith(f"@{corporate_email_domain()}")


def _contains_cyrillic(value: str) -> bool:
    return any("\u0400" <= char <= "\u04FF" for char in value)


def _transliterate_token(token: str) -> str:
    token = token.lower()
    if not _contains_cyrillic(token):
        return token
    parts: list[str] = []
    for char in token:
        if char in _CYR_TO_LAT:
            parts.append(_CYR_TO_LAT[char])
        elif char.isascii() and char.isalnum():
            parts.append(char)
    return "".join(parts)


def _transliterate_name(name: str) -> str:
    return " ".join(_transliterate_token(part) for part in normalize_name(name).split())


def _latin_query_variants(fio: str) -> list[str]:
    if not _contains_cyrillic(fio):
        return []
    latin = _transliterate_name(fio)
    if not latin or latin == normalize_name(fio):
        return []
    parts = latin.split()
    variants = [latin]
    if parts:
        variants.append(parts[0])
    return [variant for variant in variants if variant]


def _looks_like_initials_first(parts: list[str]) -> bool:
    if not parts:
        return False
    first = parts[0]
    if "." in first:
        return True
    normalized = normalize_name(first).split()
    return bool(normalized) and all(len(token) == 1 for token in normalized)


def _gal_queries(fio: str) -> list[str]:
    query = fio.strip()
    if not query:
        return []
    queries = [query]
    parts = query.split()
    if len(parts) >= 2:
        short = f"{parts[0]} {parts[1]}"
        if short not in queries:
            queries.append(short)
    if len(parts) >= 3 and not _looks_like_initials_first(parts):
        dotted = f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
        reverse = f"{parts[1][0]}.{parts[2][0]}. {parts[0]}"
        for variant in (dotted, reverse):
            if variant not in queries:
                queries.append(variant)
    elif len(parts) == 2 and not _looks_like_initials_first(parts):
        dotted = f"{parts[0]} {parts[1][0]}."
        reverse = f"{parts[1][0]}. {parts[0]}"
        for variant in (dotted, reverse):
            if variant not in queries:
                queries.append(variant)
    surname_query = parts[-1] if _looks_like_initials_first(parts) else parts[0]
    if surname_query and surname_query not in queries:
        queries.append(surname_query)
    for variant in _latin_query_variants(query):
        if variant not in queries:
            queries.append(variant)
    return queries


def _is_initial_token(part: str) -> bool:
    return len(part) == 1


def _initials_key(parts: list[str]) -> str:
    return "".join(part[0] for part in parts if part)


def _name_identities(name: str) -> list[tuple[str, str]]:
    """Возможные (фамилия, инициалы) для сравнения ФИО в разных форматах."""
    parts = normalize_name(name).split()
    if len(parts) < 2:
        return []

    identities: list[tuple[str, str]] = []

    def add(surname: str, initials_parts: list[str]) -> None:
        surname = surname.strip()
        if not surname:
            return
        initials = _initials_key(initials_parts)
        key = (surname, initials)
        if key not in identities:
            identities.append(key)

    if _is_initial_token(parts[0]):
        add(parts[-1], parts[:-1])
        return identities

    if all(_is_initial_token(part) for part in parts[1:]):
        add(parts[0], parts[1:])
        return identities

    add(parts[0], parts[1:])

    if len(parts) == 3 and not _is_initial_token(parts[0]) and not _is_initial_token(parts[1]):
        add(parts[2], parts[:2])

    return identities


def _initials_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    left_latin = _transliterate_token(left)
    right_latin = _transliterate_token(right)
    if left_latin == right_latin:
        return True
    return left_latin.startswith(right_latin) or right_latin.startswith(left_latin)


def _surnames_match(left: str, right: str) -> bool:
    if left == right:
        return True
    left_latin = _transliterate_token(left)
    right_latin = _transliterate_token(right)
    if not left_latin or not right_latin:
        return False
    return left_latin == right_latin


def _token_matches_query_token(query_token: str, name_token: str) -> bool:
    if not query_token or not name_token:
        return False
    if query_token == name_token:
        return True
    if name_token.startswith(query_token) or query_token.startswith(name_token):
        return True
    if len(query_token) == 1 and name_token.startswith(query_token):
        return True
    left_latin = _transliterate_token(query_token)
    right_latin = _transliterate_token(name_token)
    if left_latin and right_latin:
        if left_latin == right_latin:
            return True
        if right_latin.startswith(left_latin) or left_latin.startswith(right_latin):
            return True
        if len(query_token) == 1 and right_latin.startswith(left_latin):
            return True
    return False


def _name_matches_query(query: str, resolved_name: str) -> bool:
    if _name_matches_fio(query, resolved_name):
        return True
    query_parts = normalize_name(query).split()
    name_parts = normalize_name(resolved_name).split()
    if not query_parts or not name_parts:
        return False
    return all(
        any(_token_matches_query_token(query_part, name_part) for name_part in name_parts)
        for query_part in query_parts
    )


def _suggestion_score(query: str, resolved_name: str) -> int:
    if _name_matches_fio(query, resolved_name):
        return 1000
    query_norm = normalize_name(query)
    name_norm = normalize_name(resolved_name)
    if name_norm.startswith(query_norm):
        return 500 + len(query_norm)
    query_parts = query_norm.split()
    name_parts = name_norm.split()
    score = 0
    if query_parts and name_parts and query_parts[0] == name_parts[0]:
        score += 300
    elif query_parts and name_parts and _surnames_match(query_parts[0], name_parts[0]):
        score += 200
    score += sum(
        10
        for query_part in query_parts
        if any(_token_matches_query_token(query_part, name_part) for name_part in name_parts)
    )
    return score


def _name_matches_fio(fio: str, resolved_name: str) -> bool:
    target = normalize_name(fio)
    name = normalize_name(resolved_name)
    if not target or not name:
        return False
    if name == target:
        return True

    target_parts = target.split()
    name_parts = name.split()
    if (
        len(target_parts) >= 2
        and len(name_parts) >= 2
        and target_parts[0] == name_parts[0]
        and target_parts[1] == name_parts[1]
    ):
        return True

    target_identities = _name_identities(fio)
    resolved_identities = _name_identities(resolved_name)
    for target_surname, target_initials in target_identities:
        for resolved_surname, resolved_initials in resolved_identities:
            if not _surnames_match(target_surname, resolved_surname):
                continue
            if _initials_match(target_initials, resolved_initials):
                return True
    return False


def _resolved_display_name(item: Any) -> str:
    mailbox = item[0] if isinstance(item, tuple) else item
    name = _mailbox_name(mailbox)
    if name:
        return name
    if isinstance(item, tuple) and len(item) > 1:
        contact = item[1]
        return (getattr(contact, "display_name", "") or "").strip()
    return ""


def _mailbox_name(mailbox: Any) -> str:
    return (getattr(mailbox, "name", "") or "").strip()


def _mailbox_email(mailbox: Any) -> str:
    return normalize_email(getattr(mailbox, "email_address", "") or "")


def load_exchange_gal_emails_for_fio(
    fio: str,
    *,
    account: Account | None = None,
    config: OutlookConfig | None = None,
) -> list[dict[str, str]]:
    """Ищет SMTP в OWA/Exchange GAL через EWS ResolveNames."""
    config = config or load_config()
    if not config.email or not config.password:
        return []

    try:
        account = account or connect_account(config)
    except (ValueError, OSError):
        return []

    entries: list[dict[str, str]] = []
    seen_emails: set[str] = set()
    for query in _gal_queries(fio):
        try:
            matches = account.protocol.resolve_names([query], return_full_contact_data=True)
        except Exception:
            continue
        for item in matches or []:
            resolved_name = _resolved_display_name(item)
            mailbox = item[0] if isinstance(item, tuple) else item
            email = _mailbox_email(mailbox)
            if not is_valid_email(email) or not is_corporate_email(email):
                continue
            if not _name_matches_fio(fio, resolved_name):
                continue
            if email in seen_emails:
                continue
            seen_emails.add(email)
            entries.append(
                {
                    "email": email,
                    "account_key": "",
                    "source": EXCHANGE_GAL_SOURCE,
                }
            )
    return entries


MAX_GAL_SUGGESTIONS = 10


def search_exchange_gal_users(
    query: str,
    *,
    account: Account | None = None,
    config: OutlookConfig | None = None,
    limit: int = MAX_GAL_SUGGESTIONS,
) -> list[dict[str, str]]:
    """Подсказки по частичному ФИО через Exchange GAL (без строгого совпадения)."""
    normalized_query = query.strip()
    if not normalized_query:
        return []

    config = config or load_config()
    if not config.email or not config.password:
        return []

    try:
        account = account or connect_account(config)
    except (ValueError, OSError):
        return []

    candidates: list[tuple[int, str, str]] = []
    seen_emails: set[str] = set()
    for gal_query in _gal_queries(normalized_query):
        try:
            matches = account.protocol.resolve_names([gal_query], return_full_contact_data=True)
        except Exception:
            continue
        for item in matches or []:
            resolved_name = _resolved_display_name(item)
            mailbox = item[0] if isinstance(item, tuple) else item
            email = _mailbox_email(mailbox)
            if not is_valid_email(email) or not is_corporate_email(email):
                continue
            if not resolved_name or not _name_matches_query(normalized_query, resolved_name):
                continue
            if email in seen_emails:
                continue
            seen_emails.add(email)
            candidates.append(
                (_suggestion_score(normalized_query, resolved_name), resolved_name, email)
            )

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"fio": resolved_name, "email": email}
        for _score, resolved_name, email in candidates[: max(limit, 1)]
    ]


def pick_exact_exchange_gal_user(
    query: str,
    candidates: list[dict[str, str]],
) -> dict[str, str] | None:
    """Точное совпадение для активации кнопки «Добавить»."""
    normalized_query = query.strip()
    if not normalized_query:
        return None
    strict = [
        candidate
        for candidate in candidates
        if _name_matches_fio(normalized_query, candidate.get("fio", ""))
    ]
    if len(strict) == 1:
        return strict[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def search_result_message(
    *,
    found: bool,
    already_added: bool,
    suggestions_count: int,
) -> str | None:
    if found:
        if already_added:
            return "Участник уже в списке"
        return None
    if suggestions_count > 1:
        return "Выберите участника из списка"
    if suggestions_count == 1:
        return None
    return "Не найден в Outlook"


def dispatch_search_exchange_gal_users(
    query: str,
    *,
    limit: int = MAX_GAL_SUGGESTIONS,
) -> list[dict[str, str]]:
    exchange_account = None
    try:
        outlook_config = load_config()
        if outlook_config.email and outlook_config.password:
            exchange_account = connect_account(outlook_config)
    except (ValueError, OSError):
        exchange_account = None
    return search_exchange_gal_users(
        query,
        account=exchange_account,
        limit=limit,
    )
