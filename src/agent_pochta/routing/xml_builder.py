"""Формирование XML document (ТЗ §12)."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agent_pochta.routing.models import RoutingDecision, ServiceRoute
from agent_pochta.routing.organizations import DIRECTION_DEFAULT
from agent_pochta.schemas import EmailMessage, SpamResult

_DEPT_CODE_RE = re.compile(r"^00-\d{6}$")
_THEME_MAX_LEN = 200
RESERVE_DEPARTMENT_CODE = "00-000066"
SPAM_DEPARTMENT_CODE = "00-999999"
_MSK = ZoneInfo("Europe/Moscow")
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
        "info_strict",
        "info_strict_unclear",
        "gazprom_np_reply",
    }
)

_ACTION_THEME_RE = re.compile(
    r"^([А-Яа-яA-Za-z][А-Яа-яA-Za-z\s]{0,30}):\s*(.+)$",
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


def _theme_context(subject: str = "", combined_text: str = "") -> str:
    return f"{subject} {combined_text}".lower()


def key_phrase_to_action(key_phrase: str) -> str | None:
    """Преобразует ключевую фразу (от LLM или эвристики) в префикс действия."""
    phrase = (key_phrase or "").strip().lower()
    if not phrase:
        return None
    if phrase.startswith(("претенз", "исков")):
        return "Решить"
    if "оплат" in phrase:
        return "Оплатить"
    if "провер" in phrase:
        return "Проверить"
    if "соглас" in phrase or "утверд" in phrase:
        return "Согласовать"
    if "ознаком" in phrase or "уведом" in phrase:
        return "Ознакомиться"
    if phrase.startswith("рассмотр"):
        return "Рассмотреть"
    if phrase.startswith("запрос"):
        return "Запрос"
    return None


def infer_theme_action(
    subject: str = "",
    combined_text: str = "",
    *,
    process_type: str = "",
    claim: bool = False,
    key_phrase: str = "",
) -> str:
    """Определяет требуемое действие для темы 1С по subject/ключевым словам/process_type."""
    from_key = key_phrase_to_action(key_phrase)
    if from_key:
        return from_key

    if (key_phrase or "").strip().lower().startswith("диалог"):
        return "Диалог"

    combined = _theme_context(subject, combined_text)
    subj = sanitize_theme(subject, max_len=80)
    subj_lower = subj.lower() if subj != "Без темы" else subject.lower()

    if claim or "претенз" in combined:
        return "Решить"
    if "иск" in combined and "риск" not in combined and "исключ" not in combined:
        return "Решить"
    if any(
        marker in combined
        for marker in ("проверить", "проверка", "неподписан", "подписать в эдо", "в эдо")
    ):
        return "Проверить"
    if any(marker in combined for marker in ("согласова", "на согласован", "утвердить", "утвержден")):
        return "Согласовать"
    if "оплат" in combined and ("счёт" in combined or "счет" in combined):
        return "Оплатить"
    if any(
        marker in combined
        for marker in (
            "уведомлен",
            "информ",
            "для сведения",
            "к сведению",
            "ознаком",
            "статус отгруз",
            "сроки отгруз",
        )
    ):
        return "Ознакомиться"
    if "просч" in combined and ("ол " in combined or "ол," in subj_lower):
        return "Отправить в просчёт"
    if any(marker in combined for marker in ("рассмотреть", "рассмотрение", "требует решения")):
        return "Рассмотреть"

    process = (process_type or "").strip().lower()
    if process == "ознакомление":
        return "Ознакомиться"
    if process == "рассмотрение":
        return "Решить" if claim else "Рассмотреть"
    return "Запрос"


def format_action_theme(action: str, subject: str) -> str:
    """Формат «Действие требуемое в письме: краткая тема» без тела и LLM-описания."""
    cleaned = sanitize_theme(subject)
    if cleaned == "Без темы":
        return cleaned
    action = (action or "Запрос").strip()
    match = _ACTION_THEME_RE.match(cleaned)
    if match and match.group(1).strip().lower() == action.lower():
        return sanitize_theme(cleaned)
    if match:
        cleaned = match.group(2).strip()
    return sanitize_theme(f"{action}: {cleaned}")


def build_action_xml_theme(
    subject: str,
    *,
    combined_text: str = "",
    process_type: str = "",
    claim: bool = False,
    key_phrase: str = "",
) -> str:
    """Theme для XML/1С: префикс требуемого действия + subject, без тела письма."""
    action = infer_theme_action(
        subject,
        combined_text,
        process_type=process_type,
        claim=claim,
        key_phrase=key_phrase,
    )
    return format_action_theme(action, subject)


def _subject_matches_theme_part(theme_part: str, subject: str) -> bool:
    theme_part = sanitize_theme(theme_part)
    subject_clean = sanitize_theme(subject)
    if theme_part == "Без темы" or subject_clean == "Без темы":
        return False
    return theme_part.lower() == subject_clean.lower()


def is_corrupted_theme(theme: str, subject: str) -> bool:
    """Тема с телом письма, приклеенным к subject (старый баг)."""
    cleaned = sanitize_theme(theme)
    subject_clean = sanitize_theme(subject)
    if cleaned == "Без темы" or subject_clean == "Без темы":
        return False
    match = _ACTION_THEME_RE.match(cleaned)
    if match:
        cleaned = match.group(2).strip()
    if cleaned.startswith(subject_clean) and len(cleaned) > len(subject_clean) + 5:
        rest = cleaned[len(subject_clean) :].strip()
        return bool(rest) and not rest.startswith(":")
    return False


def resolve_document_theme(
    email: EmailMessage,
    *,
    explicit_theme: str = "",
    combined_text: str = "",
    process_type: str = "",
    claim: bool = False,
) -> str:
    """Единая тема для XML и OData: действие + subject, без тела письма."""
    text = combined_text if combined_text is not None else (email.body_text or "")
    explicit = sanitize_theme(explicit_theme)
    if explicit != "Без темы" and not is_corrupted_theme(explicit, email.subject or ""):
        match = _ACTION_THEME_RE.match(explicit)
        if match and _subject_matches_theme_part(match.group(2), email.subject or ""):
            return explicit
    return build_action_xml_theme(
        email.subject or "",
        combined_text=text,
        process_type=process_type,
        claim=claim,
    )


def email_subject_theme(
    email: EmailMessage,
    *,
    fallback: str = "",
    combined_text: str = "",
    process_type: str = "",
    claim: bool = False,
) -> str:
    """Тема для XML/1С: действие + subject, без тела и LLM-описания."""
    return build_action_xml_theme(
        email.subject or fallback,
        combined_text=combined_text or email.body_text or "",
        process_type=process_type,
        claim=claim,
    )


def build_subject_xml_theme(
    subject: str,
    *,
    combined_text: str = "",
    process_type: str = "",
    claim: bool = False,
) -> str:
    """Theme для XML из subject: префикс действия без тела письма."""
    return build_action_xml_theme(
        subject,
        combined_text=combined_text,
        process_type=process_type,
        claim=claim,
    )


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


def normalize_xml_theme(
    raw: str,
    *,
    subject: str = "",
    combined_text: str = "",
    process_type: str = "",
    claim: bool = False,
) -> str:
    """Нормализует xml_theme от LLM в формат «Действие требуемое: subject» без тела письма."""
    theme = sanitize_theme(raw)
    if theme == "Без темы":
        return build_action_xml_theme(
            subject,
            combined_text=combined_text,
            process_type=process_type,
            claim=claim,
        )

    key_phrase = ""
    if " - " in theme:
        _, key_phrase = theme.rsplit(" - ", 1)
    elif _ACTION_THEME_RE.match(theme):
        return theme

    return build_action_xml_theme(
        subject or theme,
        combined_text=combined_text,
        process_type=process_type,
        claim=claim,
        key_phrase=key_phrase or categorize_theme_key_phrase(subject, combined_text),
    )


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


def _format_mail_datetime_for_xml(received_at: datetime) -> str:
    """mail_datetime в XML: received_at (UTC) в Europe/Moscow без tz suffix."""
    dt = received_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    msk = dt.astimezone(_MSK)
    return msk.replace(tzinfo=None, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


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

    mail_dt = _format_mail_datetime_for_xml(email.received_at)
    document_process = (
        (decision.process or "").strip()
        or (services[0].process if services else "")
        or "исполнение"
    )
    if decision.theme:
        theme = resolve_document_theme(
            email,
            explicit_theme=decision.theme,
            process_type=document_process,
            claim=decision.claim,
        )
    else:
        theme = build_subject_xml_theme(
            email.subject or "",
            process_type=document_process,
            claim=decision.claim,
        )
    partner = format_partner(decision.partner)

    organization = (decision.organization or "НП").strip() or "НП"
    direction = (decision.direction or DIRECTION_DEFAULT).strip() or DIRECTION_DEFAULT
    dialog_block = ""
    if decision.dialog_mode:
        dialog_block = f"<dialog_mode>{_esc(decision.dialog_mode)}</dialog_mode>"

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
        f"{dialog_block}"
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
