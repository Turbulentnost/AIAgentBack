"""Формирование XML document (ТЗ §12)."""

from __future__ import annotations

import html
import re
from datetime import datetime

from agent_pochta.routing.models import RoutingDecision, ServiceRoute
from agent_pochta.schemas import EmailMessage, SpamResult

_DEPT_CODE_RE = re.compile(r"^00-\d{6}$")
_THEME_MAX_LEN = 200
RESERVE_DEPARTMENT_CODE = "00-000066"
SPAM_DEPARTMENT_CODE = "00-999999"
_INTERNAL_KEYWORD_SOURCES = frozenset(
    {
        "exact_email",
        "email_keyword",
        "content",
        "sales_orkk",
        "sales_gazprom",
        "sales_odp",
        "reserve",
        "human_correction",
    }
)


def _esc(value: str) -> str:
    return html.escape(value or "", quote=False)


def resolve_service_code(
    code: str,
    name: str = "",
    *,
    fallback: str = RESERVE_DEPARTMENT_CODE,
) -> str:
    """В XML services/name допускается только код подразделения 1С (00-XXXXXX)."""
    for candidate in (code, name):
        value = (candidate or "").strip()
        if _DEPT_CODE_RE.match(value):
            return value
    return fallback


def sanitize_theme(theme: str, *, max_len: int = _THEME_MAX_LEN) -> str:
    """Очищает тему от служебных тегов и лишних пробелов."""
    if not theme or not str(theme).strip():
        return "Без темы"
    cleaned = str(theme)
    cleaned = re.sub(
        r"<(?:think(?:ing)?|analysis|comment|redacted_thinking)[^>]*>.*?</(?:think(?:ing)?|analysis|comment|redacted_thinking)>",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = strip_forbidden_tags(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^(re:|fw:|fwd:)\s*", "", cleaned, flags=re.IGNORECASE)
    if not cleaned:
        return "Без темы"
    return cleaned[:max_len]


def categorize_theme_key_phrase(subject: str, text: str = "") -> str:
    """Краткая категория запроса для поля theme в XML (заглушка без LLM)."""
    combined = f"{subject} {text}".lower()
    clean_subject = sanitize_theme(subject, max_len=80)
    label = clean_subject if clean_subject != "Без темы" else (subject or "").strip()

    if "претенз" in combined:
        return f"Претензия по {label.lower()}" if label else "Претензия"
    if "иск" in combined and "риск" not in combined and "исключ" not in combined:
        return f"Исковое требование по {label.lower()}" if label else "Исковое требование"
    if "счёт" in combined or "счет" in combined:
        if "оплат" in combined:
            return "Счёт на оплату"
        return "Запрос на выставление счёта"
    if "акт свер" in combined:
        return "Запрос акта сверки"
    if "договор" in combined:
        return f"Запрос по договору" if not label else f"Запрос по договору: {label.lower()}"
    if "ткп" in combined or "коммерческ" in combined:
        return f"Запрос ТКП" if not label else f"Запрос ТКП: {label.lower()}"
    if label:
        return f"Запрос на {label.lower()}"
    return "Запрос"


def build_stub_xml_theme(subject: str, combined_text: str = "") -> str:
    """Формирует theme для XML без LLM: «описание сути — ключевая фраза»."""
    clean_subject = sanitize_theme(subject, max_len=80)
    if clean_subject == "Без темы":
        clean_subject = ""

    body = (combined_text or "").strip()
    description = ""
    if body:
        snippet = body[:300]
        for sep in (". ", "! ", "? ", "\n"):
            idx = snippet.find(sep)
            if idx > 20:
                description = snippet[: idx + 1].strip()
                break
        if not description:
            description = snippet[:120].strip()
            if len(body) > 120:
                description = description.rstrip(".,;") + "..."

    if not description:
        if clean_subject:
            description = f"Письмо по теме «{clean_subject}»"
        else:
            description = "Входящее письмо требует обработки"

    key_phrase = categorize_theme_key_phrase(subject, body)
    return sanitize_theme(f"{description} - {key_phrase}")


def normalize_xml_theme(raw: str, *, subject: str = "", combined_text: str = "") -> str:
    """Нормализует xml_theme от LLM; при отсутствии « - » дополняет ключевой фразой."""
    theme = sanitize_theme(raw)
    if theme == "Без темы":
        return build_stub_xml_theme(subject, combined_text)
    if " - " not in theme:
        key_phrase = categorize_theme_key_phrase(subject or theme, combined_text)
        theme = sanitize_theme(f"{theme} - {key_phrase}")
    return theme


def format_partner(partner: str | None) -> str:
    """Контрагент из справочника; при отсутствии — «-» (ТЗ §12)."""
    value = (partner or "").strip()
    if not value or value == "-":
        return "-"
    return value


def format_matching_keywords(keywords: list[str]) -> str:
    """Человекочитаемые ключевые слова через «; » без внутренних имён правил."""
    seen: set[str] = set()
    parts: list[str] = []
    for raw in keywords:
        kw = (raw or "").strip()
        if not kw or kw in seen or kw in _INTERNAL_KEYWORD_SOURCES:
            continue
        seen.add(kw)
        parts.append(kw)
    return "; ".join(parts)


def service_reasoning(email: EmailMessage, decision: RoutingDecision) -> str:
    """Обоснование маршрута — название (тема) письма."""
    return sanitize_theme(email.subject or decision.theme or "")


def _ensure_service_routes(
    decision: RoutingDecision,
    *,
    is_spam: bool,
) -> list[ServiceRoute]:
    if is_spam:
        return [
            ServiceRoute(
                code=SPAM_DEPARTMENT_CODE,
                name="Спам",
                process="ознакомление",
                direction=decision.direction,
            )
        ]
    services = list(decision.services)
    if not services:
        return [
            ServiceRoute(
                code=RESERVE_DEPARTMENT_CODE,
                name=RESERVE_DEPARTMENT_CODE,
                process="исполнение",
                direction=decision.direction,
            )
        ]
    return services


def _service_title_block(service_code: str, department_name: str) -> str:
    title = (department_name or "").strip()
    if not title or title == service_code or _DEPT_CODE_RE.match(title):
        return ""
    return f"<title>{_esc(title)}</title>"


def build_xml_document(
    email: EmailMessage,
    *,
    recipient: str,
    decision: RoutingDecision,
    spam: SpamResult | None = None,
) -> str:
    is_spam = bool(spam and spam.is_spam)
    services = _ensure_service_routes(decision, is_spam=is_spam)
    reasoning = service_reasoning(email, decision)

    service_blocks = []
    for svc in services:
        service_code = resolve_service_code(
            svc.code,
            svc.name,
            fallback=SPAM_DEPARTMENT_CODE if is_spam else RESERVE_DEPARTMENT_CODE,
        )
        service_blocks.append(
            "<service>"
            f"<name>{_esc(service_code)}</name>"
            f"{_service_title_block(service_code, svc.name)}"
            f"<process>{_esc(svc.process or 'исполнение')}</process>"
            f"<reasoning>{_esc(reasoning)}</reasoning>"
            "</service>"
        )

    mail_dt = email.received_at.strftime("%Y-%m-%d %H:%M:%S")
    theme = sanitize_theme(decision.theme or email.subject or "")
    partner = format_partner(decision.partner)

    organization = (decision.organization or "НП").strip() or "НП"
    direction = (decision.direction or "КС").strip() or "КС"
    document_process = (
        (decision.process or "").strip()
        or (services[0].process if services else "")
        or "исполнение"
    )

    return (
        "<document>"
        f"<organization>{_esc(organization)}</organization>"
        f"<theme>{_esc(theme)}</theme>"
        f"<направление>{_esc(direction)}</направление>"
        f"<claim>{'true' if decision.claim else 'false'}</claim>"
        f"<partner>{_esc(partner)}</partner>"
        f"<services>{''.join(service_blocks)}</services>"
        f"<email_sender>{_esc(email.sender_email)}</email_sender>"
        f"<email_recipient>{_esc(recipient)}</email_recipient>"
        f"<mail_datetime>{mail_dt}</mail_datetime>"
        f"<process>{_esc(document_process)}</process>"
        "</document>"
    )


def validate_xml_document(xml: str) -> bool:
    """Минимальная валидация обязательных тегов (ТЗ §12)."""
    required = (
        "organization",
        "theme",
        "направление",
        "claim",
        "partner",
        "services",
        "email_sender",
        "email_recipient",
        "mail_datetime",
        "process",
    )
    if not (all(f"<{tag}>" in xml for tag in required) and xml.strip().startswith("<document>")):
        return False

    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml.strip())
    except ET.ParseError:
        return False

    if root.tag != "document":
        return False

    services = root.find("services")
    if services is None:
        return False
    service_nodes = services.findall("service")
    if not service_nodes:
        return False
    has_valid_department = False
    for service in service_nodes:
        code = (service.findtext("name") or "").strip()
        if not code:
            return False
        if not _DEPT_CODE_RE.match(code):
            return False
        has_valid_department = True
    return has_valid_department


def strip_forbidden_tags(xml: str) -> str:
    forbidden = re.compile(
        r"</?(think(?:ing)?|analysis|comment|redacted_thinking)[^>]*>",
        re.I,
    )
    return forbidden.sub("", xml)
