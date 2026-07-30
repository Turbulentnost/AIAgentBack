"""Human-in-the-loop: разделение причин маршрутизации и спама."""

from __future__ import annotations

import json

from agent_pochta.schemas import ProcessingStatus

_ROUTING_ESCALATION_MARKERS = (
    "уверенность маршрута",
    "конфликт нескольких правил",
    "режим review",
    "требуется подтверждение оператора",
    "восстановлено из спама: требуется",
)

_SPAM_GRAY_ZONE_MARKER = "спам в серой зоне"


def is_routing_escalation_reason(reason: str | None) -> bool:
    """True, если причина относится к проверке маршрута, а не к спаму."""
    text = (reason or "").lower().strip()
    if not text:
        return False
    return any(marker in text for marker in _ROUTING_ESCALATION_MARKERS)


def is_spam_gray_zone_reason(reason: str | None) -> bool:
    text = (reason or "").lower().strip()
    return _SPAM_GRAY_ZONE_MARKER in text


def hitl_reason_from_row(row) -> str | None:
    """Причина эскалации HITL: hitl_reason в payload или legacy spam_reason."""
    payload: dict = {}
    if row.raw_payload_json:
        try:
            loaded = json.loads(row.raw_payload_json)
            if isinstance(loaded, dict):
                payload = loaded
        except json.JSONDecodeError:
            payload = {}

    hitl = payload.get("hitl_reason")
    if isinstance(hitl, str) and hitl.strip():
        return hitl.strip()

    spam_reason = getattr(row, "spam_reason", None)
    if isinstance(spam_reason, str) and spam_reason.strip():
        return spam_reason.strip()
    return None


def row_requires_routing_review(row) -> bool:
    """Письмо ждёт подтверждения отдела, а не решения по спаму."""
    if row.status != ProcessingStatus.AWAITING_HUMAN.value:
        return False
    return is_routing_escalation_reason(hitl_reason_from_row(row))


def row_requires_spam_review(row) -> bool:
    """Письмо в серой зоне спама (mark_spam / mark_not_spam)."""
    if row.status != ProcessingStatus.AWAITING_HUMAN.value:
        return False
    reason = hitl_reason_from_row(row)
    if is_routing_escalation_reason(reason):
        return False
    return is_spam_gray_zone_reason(reason) or bool(row.is_spam)


def parse_recipient_from_message_id(message_id: str) -> str | None:
    """Извлекает routing_recipient из составного message_id (<id>#mailbox@domain)."""
    if "#" not in message_id:
        return None
    recipient = message_id.rsplit("#", 1)[-1].strip().lower()
    if "@" in recipient:
        return recipient
    return None


def resolve_department_from_recipient(recipient: str) -> tuple[str, str] | None:
    """Подбирает отдел по local-part получателя (email_keyword_rules)."""
    from agent_pochta.services.routing_departments import load_routing_rules

    recipient = recipient.lower().strip()
    if "@" not in recipient:
        return None
    local = recipient.split("@", 1)[0]
    rules = load_routing_rules()
    best: tuple[str, str] | None = None
    best_len = 0
    for rule in rules.get("email_keyword_rules") or []:
        keyword = str(rule.get("keyword") or "").lower()
        if not keyword or keyword not in local:
            continue
        if len(keyword) > best_len:
            best_len = len(keyword)
            best = (str(rule["code"]), str(rule.get("name") or rule["code"]))
    if best:
        return best

    for rule in rules.get("exact_email_rules") or []:
        if rule.get("is_fallback_mailbox"):
            continue
        if recipient == str(rule.get("email") or "").lower():
            return str(rule["code"]), str(rule.get("name") or rule["code"])
    return None
