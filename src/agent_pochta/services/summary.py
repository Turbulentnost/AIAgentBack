"""Формирование и пост-обработка краткого обзора письма (узел 6)."""

from __future__ import annotations

import re
from typing import Any

from agent_pochta.config import Settings, get_settings
from agent_pochta.schemas import EmailMessage, RoutingResult, SenderIdentity

_SIGNATURE_RE = re.compile(
    r"\n\s*(-{2,}|_{4,}|с уважением|best regards|kind regards|sent from my|отправлено с iphone)",
    re.IGNORECASE,
)
_SIGNATURE_START_RE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"с\s+уважением|с\s+уважением\s+и|best\s+regards|kind\s+regards|"
    r"respectfully|regards|thank\s+you|спасибо"
    r")",
    re.IGNORECASE,
)
_LEGAL_FORM_RE = re.compile(
    r"(?<!\w)("
    r"(?:ООО|OOO|АО|AO|ПАО|PAO|ЗАО|ZAO|ИП|ФГУП|"
    r"ГУП|МУП|НКО|АНО|ЧОУ|ОАО|"
    r"LLC|Ltd\.?|Inc\.?|Corp\.?)"
    r"[\s«\"]*[^\n,;]{2,120}?)"
    r"(?:[»\"]|(?=\s*(?:\n|$|tel|phone|тел|моб|email|@|\d{10})))",
    re.IGNORECASE,
)
_PERSON_NAME_RE = re.compile(
    r"^[А-ЯЁA-Z][а-яёa-z]+(?:\s+[А-ЯЁA-Z]\.?)?(?:\s+[А-ЯЁA-Z][а-яёa-z]+)?\.?$"
)
_CONTACT_LINE_MARKERS = ("тел", "phone", "email", "www", "http", "факс", "fax", "моб")


def extract_email_signature_tail(text: str, *, max_tail_chars: int = 2000) -> str:
    """Возвращает хвост письма — подпись или последние строки тела."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""
    tail = normalized[-max_tail_chars:]
    match = _SIGNATURE_START_RE.search(tail)
    if match:
        return tail[match.start() :].strip()
    return tail


def extract_partner_from_signature(text: str) -> str | None:
    """Извлекает наименование организации из подписи в конце письма."""
    from agent_pochta.services.llm_analyze import normalize_partner_name

    signature = extract_email_signature_tail(text)
    if not signature:
        return None

    matches = list(_LEGAL_FORM_RE.finditer(signature))
    if matches:
        return normalize_partner_name(matches[-1].group(0).strip())

    match = _SIGNATURE_START_RE.search(signature)
    if not match:
        return None

    for line in signature[match.end() :].split("\n"):
        line = line.strip()
        if not line or len(line) < 3:
            continue
        if _PERSON_NAME_RE.match(line):
            continue
        if any(marker in line.lower() for marker in _CONTACT_LINE_MARKERS):
            continue
        partner = normalize_partner_name(line)
        if partner:
            return partner
    return None


def prepare_text_for_summary(text: str, *, max_chars: int = 12_000) -> str:
    """Нормализует и слегка укорачивает исходный текст перед LLM."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    match = _SIGNATURE_RE.search(normalized)
    if match and match.start() > 40:
        normalized = normalized[: match.start()].strip()
    if len(normalized) > max_chars:
        normalized = normalized[:max_chars].rsplit("\n", 1)[0].strip()
    return normalized


def _build_attachments_text_from_email(email: EmailMessage) -> str:
    from agent_pochta.attachments.extract import is_meaningful_extracted_text
    from agent_pochta.attachments.pipeline import ATTACHMENTS_HEADER, attachment_placeholder

    if not email.attachments:
        return ""

    parts: list[str] = []
    for att in email.attachments:
        if att.extracted_text and is_meaningful_extracted_text(att.extracted_text):
            parts.append(f"--- {att.filename} ({att.mime_type}) ---\n{att.extracted_text}")
        elif att.filename:
            parts.append(attachment_placeholder(att))

    if not parts:
        return ""
    return f"{ATTACHMENTS_HEADER.format(count=len(email.attachments))}\n\n" + "\n\n".join(parts)


def build_summary_context(
    email: EmailMessage,
    combined_text: str,
    *,
    routing: RoutingResult | None = None,
    sender: SenderIdentity | None = None,
    attachments_text: str = "",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Контекст для промпта суммаризации."""
    from agent_pochta.attachments.extract import is_meaningful_extracted_text

    settings = settings or get_settings()

    attachment_names = [a.filename for a in email.attachments if a.filename]
    attachment_summaries = [
        {
            "filename": att.filename,
            "mime_type": att.mime_type,
            "ocr_used": att.ocr_used,
            "has_text": is_meaningful_extracted_text(att.extracted_text),
            "text_excerpt": (att.extracted_text or "")[:500] or None,
        }
        for att in email.attachments
    ]

    if not attachments_text:
        attachments_text = _build_attachments_text_from_email(email)

    body_source = "\n\n".join(p for p in [email.subject, email.body_text] if p)
    if not body_source.strip():
        body_source = combined_text or email.body_text

    body_only = prepare_text_for_summary(
        body_source,
        max_chars=settings.summary_body_max_chars,
    )
    attachments_section = (
        prepare_text_for_summary(
            attachments_text,
            max_chars=settings.summary_attachments_max_chars,
        )
        if attachments_text
        else ""
    )

    if attachments_section:
        body_and_attachments = f"{body_only}\n\n{attachments_section}"
    else:
        body_and_attachments = body_only

    email_signature = extract_email_signature_tail(email.body_text or combined_text or "")

    ctx: dict[str, Any] = {
        "sender_name": email.sender_name or "",
        "sender_email": email.sender_email,
        "subject": email.subject,
        "body_and_attachments": body_and_attachments,
        "email_signature": email_signature,
        "attachments_text": attachments_section,
        "attachments_count": len(email.attachments),
        "attachment_names": attachment_names,
        "attachments": attachment_summaries,
        "has_attachment_text": any(item["has_text"] for item in attachment_summaries),
    }
    if sender and sender.found and sender.contractor:
        ctx["contractor_name"] = sender.contractor.name
        ctx["contractor_type"] = sender.contractor.contractor_type
    if routing:
        ctx["department_name"] = routing.department_name
        ctx["priority"] = routing.priority.value
    return ctx


# Ответ модели «как чат-бот отправителю», а не обзор для офис-менеджера.
_CHAT_REPLY_START_RE = re.compile(
    r"^\s*(?:"
    r"здравствуйте|добр(?:ый|ое)\s+(?:день|утро|вечер)|привет(?:ствую)?|"
    r"hello|hi\b|dear\b"
    r")\b",
    re.IGNORECASE,
)
_CHAT_REPLY_MARKERS_RE = re.compile(
    r"(?:"
    r"спасибо\s+за\s+(?:ваше\s+|Ваше\s+)?сообщение|"
    r"благодар(?:ю|им)\s+за\s+(?:ваше\s+|обращение|письмо)|"
    r"вам\s+может\s+потребоваться|"
    r"вам\s+следует|"
    r"рекомендую\s+обратиться|"
    r"обратитесь\s+к\s+(?:руководителю|отделу|сотруднику)|"
    r"если\s+у\s+вас\s+есть\s+вопросы|"
    r"буду\s+рад(?:а)?\s+помочь|"
    r"чем\s+могу\s+помочь"
    r")",
    re.IGNORECASE,
)


def looks_like_chat_reply(text: str) -> bool:
    """True, если текст похож на ответ отправителю, а не на деловой обзор."""
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return False
    if _CHAT_REPLY_START_RE.search(cleaned) and _CHAT_REPLY_MARKERS_RE.search(cleaned):
        return True
    if _CHAT_REPLY_START_RE.search(cleaned) and re.search(
        r"\bвам\b|\bвас\b|\bваши?\b", cleaned, flags=re.IGNORECASE
    ):
        return True
    # Без приветствия, но явный «ассистентский» ответ адресату
    marker_hits = len(_CHAT_REPLY_MARKERS_RE.findall(cleaned))
    return marker_hits >= 2


def sanitize_summary_ru(text: str) -> str:
    """Отбрасывает chat-style ответы; иначе возвращает текст без изменений."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    if looks_like_chat_reply(cleaned):
        return ""
    return cleaned


def clamp_summary(text: str, *, max_sentences: int = 5, max_chars: int = 800) -> str:
    """Ограничивает обзор 3–5 предложениями (по ТЗ) и максимальной длиной."""
    cleaned = sanitize_summary_ru(text)
    if not cleaned:
        return cleaned
    cleaned = " ".join(cleaned.split()).strip()

    parts = re.split(r"(?<=[.!?…])\s+", cleaned)
    sentences = [s.strip() for s in parts if s.strip()]
    if len(sentences) > max_sentences:
        sentences = sentences[:max_sentences]
    result = " ".join(sentences)

    if len(result) > max_chars:
        truncated = result[: max_chars - 1]
        if " " in truncated:
            truncated = truncated.rsplit(" ", 1)[0]
        result = f"{truncated}…"
    return result


def summary_ru_system_rules(*, min_sent: int, max_sent: int) -> str:
    """Общие правила поля summary_ru для analyze_incoming и summarize_ru."""
    return (
        f"summary_ru ({min_sent}–{max_sent} предложений) — канцелярский обзор "
        "для офис-менеджера (третье лицо), не ответ отправителю.\n"
        "Включи: кто обратился; суть; что сделать внутри; важные вложения; "
        "срок — только если указан.\n"
        "Запрещено: приветствия («Здравствуйте»), благодарности "
        "(«Спасибо за ваше сообщение»), советы адресату, «чем могу помочь»."
    )
