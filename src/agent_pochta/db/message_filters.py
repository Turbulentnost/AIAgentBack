"""Фильтры списка писем (даты в часовом поясе Europe/Moscow)."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import and_, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
INFO_MAILBOX = "info@turbo-don.ru"
TEST_II_MAILBOX = "test_ii@turbo-don.ru"
INFO_RECIPIENT_Q = "info"


def parse_optional_date(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    return date.fromisoformat(value.strip())


def msk_day_start_utc(day: date) -> datetime:
    start = datetime.combine(day, time.min, tzinfo=MSK)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def msk_day_end_exclusive_utc(day: date) -> datetime:
    return msk_day_start_utc(day + timedelta(days=1))


def normalize_email_address(addr: str) -> str:
    return addr.lower().strip()


def payload_recipient_lists(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    to_raw = payload.get("to") or []
    cc_raw = payload.get("cc") or []
    to_list = [normalize_email_address(str(item)) for item in to_raw if str(item).strip()]
    cc_list = [normalize_email_address(str(item)) for item in cc_raw if str(item).strip()]
    return to_list, cc_list


def sanitize_json_text_for_pg(value: str) -> str:
    """Strip NUL from JSON text before PostgreSQL jsonb cast.

    json.dumps emits ``\\u0000`` for NUL; Postgres rejects that on text→jsonb.
    """
    if not value:
        return value
    return value.replace("\x00", "").replace("\\u0000", "")


def safe_payload_jsonb(raw_payload_column):
    """CAST text -> JSONB; strip JSON \u0000 escapes before cast (Postgres rejects them)."""
    sanitized = func.regexp_replace(raw_payload_column, r"\\u0000", "", "g")
    return cast(sanitized, JSONB)


def operator_review_state_sql_flags(raw_payload_column, email_id_column):
    """SQL-выражения corrected / verified / pending (как operator_review_state в list API)."""
    from agent_pochta.db.models import ClassificationEventRow

    payload = safe_payload_jsonb(raw_payload_column)
    is_corrected_payload = payload["operator_corrected"] == cast("true", JSONB)
    has_operator_change = (
        select(literal(1))
        .select_from(ClassificationEventRow)
        .where(
            ClassificationEventRow.email_id == email_id_column,
            ClassificationEventRow.category == "department",
            ClassificationEventRow.event_type == "operator_change",
            ClassificationEventRow.actor == "operator",
        )
        .exists()
    )
    has_operator_approve = (
        select(literal(1))
        .select_from(ClassificationEventRow)
        .where(
            ClassificationEventRow.email_id == email_id_column,
            ClassificationEventRow.category == "department",
            ClassificationEventRow.event_type == "operator_approve",
            ClassificationEventRow.actor == "operator",
        )
        .exists()
    )
    is_corrected = or_(is_corrected_payload, has_operator_change)
    is_verified_raw = or_(
        payload["operator_verified"] == cast("true", JSONB),
        has_operator_approve,
    )
    is_verified = and_(is_verified_raw, ~is_corrected)
    is_pending = and_(~is_corrected, ~is_verified_raw)
    return is_corrected, is_verified, is_pending



def _jsonb_contains_email(payload, key: str, email: str):
    """SQL: JSON-массив key содержит email (без учёта регистра)."""
    array_expr = func.coalesce(payload[key], cast("[]", JSONB))
    elem = func.jsonb_array_elements_text(array_expr).table_valued("value")
    return (
        select(literal(1))
        .select_from(elem)
        .where(func.lower(elem.c.value) == email)
        .exists()
    )


def is_info_to_test_ii_routing(*, mailbox: str, payload: dict[str, Any] | None) -> bool:
    """Письмо по цепочке info@turbo-don.ru → test_ii@turbo-don.ru."""
    if not payload:
        return False

    routing_raw = payload.get("routing_recipient")
    routing = normalize_email_address(str(routing_raw)) if routing_raw else ""
    if routing != TEST_II_MAILBOX:
        return False

    to_list, cc_list = payload_recipient_lists(payload)
    has_info = INFO_MAILBOX in to_list or INFO_MAILBOX in cc_list
    has_test_ii_to = TEST_II_MAILBOX in to_list

    if has_test_ii_to and not has_info:
        return False

    if has_info:
        return True

    if not to_list and not cc_list:
        return normalize_email_address(mailbox) == TEST_II_MAILBOX

    return False


def email_info_filter_payload(
    *,
    mailbox: str,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    routing_recipient: str | None = None,
) -> dict[str, Any]:
    """Словарь для is_only_info_to из полей письма (узел 7, retry_erp)."""
    payload: dict[str, Any] = {
        "to": list(to or []),
        "cc": list(cc or []),
    }
    if routing_recipient:
        payload["routing_recipient"] = routing_recipient
    elif normalize_email_address(mailbox) == INFO_MAILBOX:
        payload["routing_recipient"] = INFO_MAILBOX
    return payload


def email_eligible_for_erp(
    *,
    mailbox: str,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    routing_recipient: str | None = None,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Регистрация входящей в 1С ERP для маршрута info@ (в т.ч. test_ii@ ← info@)."""
    if payload is None:
        payload = email_info_filter_payload(
            mailbox=mailbox,
            to=to,
            cc=cc,
            routing_recipient=routing_recipient,
        )
    if is_only_info_to(mailbox=mailbox, payload=payload):
        return True
    if is_info_to_test_ii_routing(mailbox=mailbox, payload=payload):
        return True
    # Multi-recipient split: routing-попытка info@ при нескольких адресах в To.
    to_list, cc_list = payload_recipient_lists(payload)
    if cc_list:
        return False
    routing_raw = payload.get("routing_recipient")
    routing = normalize_email_address(str(routing_raw)) if routing_raw else ""
    return routing == INFO_MAILBOX and INFO_MAILBOX in to_list


def is_only_info_to(*, mailbox: str, payload: dict[str, Any] | None) -> bool:
    """Письмо адресовано только info@turbo-don.ru (поле Кому), без других To/Cc."""
    if not payload:
        return False

    to_list, cc_list = payload_recipient_lists(payload)
    if cc_list:
        return False

    other_in_to = [addr for addr in to_list if addr != INFO_MAILBOX]
    if other_in_to:
        return False

    routing_raw = payload.get("routing_recipient")
    routing = normalize_email_address(str(routing_raw)) if routing_raw else ""
    if routing:
        return routing == INFO_MAILBOX

    if to_list == [INFO_MAILBOX]:
        return True
    if not to_list and normalize_email_address(mailbox) == INFO_MAILBOX:
        return True
    return False


# Backward-compatible alias for tests/callers during transition.
is_only_info_recipient = is_info_to_test_ii_routing


def load_payload_dict(raw_payload_json: str | None) -> dict[str, Any] | None:
    if not raw_payload_json:
        return None
    try:
        payload = json.loads(raw_payload_json)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def recipient_display_value(*, mailbox: str, payload: dict[str, Any] | None) -> str:
    """Значение графы «Кому» в UI: routing_recipient или список To."""
    if not payload:
        return ""
    routing_raw = payload.get("routing_recipient")
    routing = str(routing_raw).strip() if routing_raw else ""
    if routing:
        return routing
    to_list, _ = payload_recipient_lists(payload)
    return ", ".join(to_list)


def matches_recipient_q(*, mailbox: str, payload: dict[str, Any] | None, query: str) -> bool:
    """Подстрока в графе «Кому» (без учёта регистра)."""
    needle = query.strip().lower()
    if not needle:
        return True
    if not payload:
        return False
    displayed = recipient_display_value(mailbox=mailbox, payload=payload).lower()
    if displayed:
        return needle in displayed
    return needle in normalize_email_address(mailbox)


def matches_info_recipient_only(*, mailbox: str, payload: dict[str, Any] | None) -> bool:
    """Outlook-style имяполучателя:(info) — «Кому» содержит info (без учёта регистра)."""
    return matches_recipient_q(mailbox=mailbox, payload=payload, query=INFO_RECIPIENT_Q)


def compute_is_info_recipient(*, mailbox: str, raw_payload_json: str | None) -> bool:
    """Denormalized flag for info_recipient_only list/stats filters."""
    return matches_info_recipient_only(
        mailbox=mailbox,
        payload=load_payload_dict(raw_payload_json),
    )


def recipient_q_sql_filter(mailbox_column, raw_payload_column, query: str):
    """SQL: подстрока в routing_recipient или (если пуст) в любом адресе To."""
    payload = safe_payload_jsonb(raw_payload_column)
    pattern = f"%{query.strip().lower()}%"

    routing = func.lower(func.coalesce(payload["routing_recipient"].astext, ""))
    routing_nonempty = func.coalesce(payload["routing_recipient"].astext, "") != ""
    routing_match = and_(routing_nonempty, routing.like(pattern))

    array_expr = func.coalesce(payload["to"], cast("[]", JSONB))
    elem = func.jsonb_array_elements_text(array_expr).table_valued("value")
    to_any_match = (
        select(literal(1))
        .select_from(elem)
        .where(func.lower(elem.c.value).like(pattern))
        .exists()
    )
    routing_empty = func.coalesce(payload["routing_recipient"].astext, "") == ""
    empty_to = func.coalesce(func.jsonb_array_length(payload["to"]), 0) == 0
    mailbox_match = and_(
        routing_empty,
        empty_to,
        func.lower(mailbox_column).like(pattern),
    )

    return and_(
        raw_payload_column.isnot(None),
        or_(routing_match, and_(routing_empty, to_any_match), mailbox_match),
    )


def info_to_test_ii_sql_filter(mailbox_column, raw_payload_column):
    """SQL-условие PostgreSQL: цепочка info@ → test_ii@."""
    payload = safe_payload_jsonb(raw_payload_column)
    routing = func.lower(func.coalesce(payload["routing_recipient"].astext, ""))

    info_in_to = _jsonb_contains_email(payload, "to", INFO_MAILBOX)
    info_in_cc = _jsonb_contains_email(payload, "cc", INFO_MAILBOX)
    test_ii_in_to = _jsonb_contains_email(payload, "to", TEST_II_MAILBOX)

    empty_to = func.coalesce(func.jsonb_array_length(payload["to"]), 0) == 0
    empty_cc = func.coalesce(func.jsonb_array_length(payload["cc"]), 0) == 0

    intake_ok = or_(
        info_in_to,
        info_in_cc,
        and_(empty_to, empty_cc, func.lower(mailbox_column) == TEST_II_MAILBOX),
    )
    not_direct_test_ii = or_(info_in_to, info_in_cc, ~test_ii_in_to)

    return and_(
        routing == TEST_II_MAILBOX,
        raw_payload_column.isnot(None),
        intake_ok,
        not_direct_test_ii,
    )


def only_info_to_sql_filter(mailbox_column, raw_payload_column):
    """SQL-условие PostgreSQL: Кому только info@turbo-don.ru, без других получателей."""
    payload = safe_payload_jsonb(raw_payload_column)
    empty_cc = func.coalesce(func.jsonb_array_length(payload["cc"]), 0) == 0

    array_expr = func.coalesce(payload["to"], cast("[]", JSONB))
    elem = func.jsonb_array_elements_text(array_expr).table_valued("value")
    has_other_to = (
        select(literal(1))
        .select_from(elem)
        .where(func.lower(elem.c.value) != INFO_MAILBOX)
        .exists()
    )
    no_foreign_to = ~has_other_to

    routing = func.lower(func.coalesce(payload["routing_recipient"].astext, ""))
    routing_is_info = routing == INFO_MAILBOX
    routing_empty = routing == ""

    info_in_to = _jsonb_contains_email(payload, "to", INFO_MAILBOX)
    empty_to = func.coalesce(func.jsonb_array_length(payload["to"]), 0) == 0

    with_routing_info = and_(routing_is_info, no_foreign_to)
    without_routing = and_(
        routing_empty,
        or_(
            and_(info_in_to, no_foreign_to),
            and_(empty_to, func.lower(mailbox_column) == INFO_MAILBOX),
        ),
    )

    return and_(
        raw_payload_column.isnot(None),
        empty_cc,
        or_(with_routing_info, without_routing),
    )


# Backward-compatible alias.
only_info_recipient_sql_filter = info_to_test_ii_sql_filter
