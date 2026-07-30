"""Маршрутизация ответов в переписке по теме ПАО Газпром (пометка «НП»)."""

from __future__ import annotations

import re

from agent_pochta.routing.normalize import keyword_in_text, normalize_text

_SUBJECT_REPLY_PREFIX_RE = re.compile(
    r"^(?:re|fw|fwd|ответ|пересылка|на)\s*:\s*",
    re.IGNORECASE,
)
_QUOTED_LINE_RE = re.compile(r"^\s*>", re.MULTILINE)
_EXCHANGE_MARKER_RE = re.compile(
    r"(?:пишет\s*:| wrote\s*:|отправлено\s*:|original message|исходное сообщение)",
    re.IGNORECASE,
)
_QUOTED_SUBJECT_RE = re.compile(
    r"(?:тема|subject)\s*:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)

_DEFAULT_GAZPROM_PATTERNS = (
    "пао газпром",
    "газпром",
    "gazprom",
)


def is_email_reply(*, subject: str, body: str) -> bool:
    """Письмо выглядит как ответ или продолжение переписки."""
    return is_reply_in_thread(subject=subject, body=body)


def is_reply_in_thread(*, subject: str, body: str) -> bool:
    """Ответ в цепочке переписки (Re:/FW:, цитаты, маркеры «пишет:/wrote:»)."""
    subj = (subject or "").strip()
    if subj and _SUBJECT_REPLY_PREFIX_RE.match(subj):
        return True

    body_norm = (body or "").replace("\r\n", "\n")
    if _QUOTED_LINE_RE.search(body_norm):
        return True
    if _EXCHANGE_MARKER_RE.search(body_norm):
        return True
    return False


def has_gazprom_mention(
    *,
    subject: str,
    body: str,
    sender_email: str = "",
    patterns: list[str] | None = None,
) -> bool:
    """Упоминание Газпрома в теме, тексте, вложениях (body) или домене отправителя."""
    text = normalize_text(f"{subject} {body} {sender_email}")
    markers = [str(p).strip() for p in (patterns or _DEFAULT_GAZPROM_PATTERNS) if str(p).strip()]
    return any(keyword_in_text(marker, text) for marker in markers)


def _marker_tag_pattern(marker: str) -> re.Pattern[str]:
    escaped = re.escape(marker.strip())
    return re.compile(
        rf"(?<![а-яёa-z0-9]){escaped}(?![а-яёa-z0-9])(?:[\s:\-\[(«»)]|$)",
        re.IGNORECASE,
    )


def _line_has_marker(line: str, marker: str, pattern: re.Pattern[str]) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    bracket_patterns = (
        re.compile(rf"\[{re.escape(marker.strip())}\]", re.IGNORECASE),
        re.compile(rf"\({re.escape(marker.strip())}\)", re.IGNORECASE),
    )
    if pattern.match(stripped) or pattern.search(stripped):
        return True
    return any(p.search(stripped) for p in bracket_patterns)


def has_np_marker_in_body(body: str, marker: str) -> bool:
    """Пометка marker в теле письма (включая цитаты и текст вложений), не в теме."""
    marker = (marker or "").strip()
    if not marker:
        return False

    body_norm = (body or "").replace("\r\n", "\n")
    if not body_norm.strip():
        return False

    pattern = _marker_tag_pattern(marker)

    for match in _QUOTED_SUBJECT_RE.finditer(body_norm):
        if _line_has_marker(match.group(1), marker, pattern):
            return True

    for line in body_norm.split("\n"):
        quoted = line.strip()
        if quoted.startswith(">"):
            quoted = quoted.lstrip(">").strip()
        if _line_has_marker(quoted, marker, pattern):
            return True

    return False


def match_gazprom_np_reply(
    *,
    subject: str,
    body: str,
    sender_email: str = "",
    rules: dict | None = None,
) -> tuple[str | None, list[str]]:
    """Ответ в переписке по Газпрому → ОПГ (НП в теле) или Операционный директор."""
    cfg = rules or {}
    if cfg is not None and cfg.get("enabled") is False:
        return None, []

    marker = str(cfg.get("marker") or "НП").strip()
    if not marker:
        return None, []

    if not is_reply_in_thread(subject=subject, body=body):
        return None, []

    gazprom_patterns = cfg.get("gazprom_content_patterns") or list(_DEFAULT_GAZPROM_PATTERNS)
    if not has_gazprom_mention(
        subject=subject,
        body=body,
        sender_email=sender_email,
        patterns=gazprom_patterns,
    ):
        return None, []

    text = normalize_text(f"{subject} {body} {sender_email}")
    for pattern in cfg.get("exclude_content_patterns") or []:
        token = str(pattern).strip().lower()
        if token and token in text:
            return None, []

    hits = ["reply", "gazprom"]
    if has_np_marker_in_body(body, marker):
        hits.append(f"np:{marker.lower()}")
        return "opg", hits

    return "operational_director", hits
