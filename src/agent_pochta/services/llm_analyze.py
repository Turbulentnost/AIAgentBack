"""Единый LLM-промпт: спам + отдел + обзор (1 API-вызов на письмо)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from agent_pochta.config import Settings, get_settings
from agent_pochta.rules.spam_context import analyze_spam_context, build_spam_llm_messages
from agent_pochta.schemas import EmailMessage, RoutingResult, SenderIdentity, SpamResult
from agent_pochta.services.summary import build_summary_context, clamp_summary

_PARTNER_MAX_LEN = 200
_EMPTY_PARTNER_MARKERS = frozenset({"-", "—", "нет", "неизвестно", "unknown", "n/a", "na", ""})
_FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yandex.ru",
        "ya.ru",
        "mail.ru",
        "inbox.ru",
        "list.ru",
        "bk.ru",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "yahoo.com",
        "icloud.com",
        "me.com",
        "proton.me",
        "protonmail.com",
    }
)


@dataclass(frozen=True)
class IncomingEmailAnalysis:
    spam: SpamResult
    routing: RoutingResult
    summary_ru: str
    xml_theme: str = ""
    partner_name: str | None = None
    process_type: str = "исполнение"


def normalize_partner_name(raw: str | None) -> str | None:
    """Нормализует наименование партнёра от LLM или справочника."""
    value = (raw or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if not value or value.lower() in _EMPTY_PARTNER_MARKERS:
        return None
    value = re.sub(r"\s+", " ", value)
    return value[:_PARTNER_MAX_LEN] or None


def infer_partner_from_email(email: EmailMessage) -> str | None:
    """Эвристика: имя отправителя или домен корпоративной почты."""
    sender_name = (email.sender_name or "").strip()
    if sender_name and "@" not in sender_name and len(sender_name) >= 3:
        if sender_name.lower() not in _EMPTY_PARTNER_MARKERS:
            return normalize_partner_name(sender_name)

    if "@" not in email.sender_email:
        return None
    domain = email.sender_email.rsplit("@", 1)[1].lower().strip()
    if not domain or domain in _FREE_EMAIL_DOMAINS:
        return None
    label = domain.split(".", 1)[0]
    if not label or label in {"mail", "info", "support", "noreply", "no-reply"}:
        return None
    return normalize_partner_name(label.replace("-", " ").title())


def resolve_partner_name(
    *,
    llm_partner: str | None,
    rag_partner: str | None,
    email: EmailMessage | None = None,
) -> str | None:
    """LLM — основной источник; RAG и эвристики — запасные."""
    llm = normalize_partner_name(llm_partner)
    if llm:
        return llm
    rag = normalize_partner_name(rag_partner)
    if rag:
        return rag
    if email is not None:
        return infer_partner_from_email(email)
    return None


def build_analyze_messages(
    email: EmailMessage,
    combined_text: str,
    candidates: list[dict],
    *,
    sender: SenderIdentity | None = None,
    skip_spam_check: bool = False,
    attachments_text: str = "",
    settings: Settings | None = None,
) -> tuple[str, str]:
    """Системный и пользовательский промпт для combined analyze."""
    settings = settings or get_settings()
    min_sent = min(3, settings.summary_max_sentences)

    spam_block = ""
    spam_json_fields = ""
    if skip_spam_check:
        spam_json_fields = (
            '"is_spam": false, "spam_confidence": 0.05, '
            '"spam_reason": "Доверенный отправитель", '
        )
    else:
        spam_block = (
            "1) Спам: is_spam, spam_confidence (0=не спам, 1=спам), spam_reason.\n"
            "   Реклама/семинары/фишинг — спам. Деловые запросы контрагентов — не спам.\n"
            "   Пересланные письма: несовпадение From и текста — норма.\n"
        )
        spam_json_fields = (
            '"is_spam": bool, "spam_confidence": float, "spam_reason": "строка", '
        )

    system = (
        "Ты помощник офис-менеджера НПО «Турбулентность-ДОН». "
        "Проанализируй входящее письмо за один ответ.\n\n"
        "Контекст включает тему, тело письма и извлечённый текст вложений "
        "(поля body_and_attachments, attachments_text). "
        "ОБЯЗАТЕЛЬНО проанализируй attachments_text: партнёр, отдел, summary_ru и xml_theme "
        "должны опираться на содержимое вложений, если там есть счета, акты, претензии, договоры "
        "или иные ключевые реквизиты. Не игнорируй вложения даже при коротком теле письма.\n\n"
        f"{spam_block}"
        "2) Отдел: выбери РОВНО один department_id из candidates; "
        "dept_confidence 0..1, reasoning.\n"
        f"3) Обзор summary_ru на русском ({min_sent}–{settings.summary_max_sentences} предложений): "
        "кто написал, суть, что сделать, важные вложения и их содержание, срок если указан.\n"
        "4) Тема xml_theme для XML-документа 1С (до 200 символов, русский язык). "
        "Формат строго: «развёрнутое описание сути запроса - ключевая фраза». "
        "Описание: 1–2 предложения — что нужно от адресата. "
        "Ключевая фраза после « - »: краткий тип запроса "
        "(«Запрос на…», «Претензия по…», «Счёт на оплату» и т.п.). "
        "Пример: «Необходимо предоставить распиновку кабеля или контакт специалиста, "
        "а также актуальную информацию о сроке мероприятия - "
        "Запрос на предоставление распиновки кабеля или контакта специалиста».\n"
        "5) Партнёр partner_name: полное наименование организации-отправителя / контрагента "
        "для поля «Партнёр» в 1С (до 200 символов). "
        "Определи из темы, текста, подписи, sender_name, sender_email и вложений "
        "(счета, акты, договоры часто содержат реквизиты организации). "
        "Если в контексте есть contractor_name из справочника — используй как подсказку, "
        "но приоритет у фактического наименования в письме. "
        "Если организация не определена — пустая строка \"\".\n"
        "6) process_type — вид процесса документа в 1С. РОВНО одно из:\n"
        "   • «рассмотрение» — требуется решение/согласование (претензии, согласования, запросы решения);\n"
        "   • «исполнение» — требуется действие (выставить счёт, подготовить документ, выполнить заказ);\n"
        "   • «ознакомление» — только информация (уведомления, сроки отгрузки, статус, FYI).\n"
        "   Пример: «Информация о сроках отгрузки», «Уведомление о поставке» → «ознакомление».\n\n"
        "Ответь строго JSON:\n"
        "{"
        f'{spam_json_fields}'
        '"department_id": "...", "department_name": "...", '
        '"dept_confidence": float, "reasoning": "...", "summary_ru": "...", '
        '"xml_theme": "описание сути - ключевая фраза", '
        '"partner_name": "ООО ... или \"\"", '
        '"process_type": "рассмотрение|исполнение|ознакомление"'
        "}"
    )

    ctx = build_summary_context(
        email,
        combined_text,
        sender=sender,
        attachments_text=attachments_text,
        settings=settings,
    )
    ctx["candidates"] = candidates
    if skip_spam_check:
        ctx["spam_check"] = "skipped_trusted_sender"
    else:
        _, spam_user = build_spam_llm_messages(email, settings)
        spam_ctx = analyze_spam_context(email, settings)
        ctx["spam_hints"] = {
            "is_forwarded": spam_ctx.is_forwarded,
            "embedded_sender": spam_ctx.embedded_sender,
            "trusted_domains": settings.trusted_domain_list,
        }
        ctx["email_for_spam"] = spam_user

    user = json.dumps(ctx, ensure_ascii=False)
    return system, user


def parse_analyze_response(
    data: dict,
    *,
    candidates: list[dict],
    skip_spam_check: bool = False,
    settings: Settings | None = None,
    subject: str = "",
    combined_text: str = "",
    claim: bool = False,
) -> IncomingEmailAnalysis:
    """Нормализует JSON от LLM в доменные объекты."""
    settings = settings or get_settings()

    if skip_spam_check:
        spam = SpamResult(
            is_spam=False,
            confidence=0.05,
            reason="Доверенный корпоративный отправитель",
            rule_hit="trusted_sender",
        )
    else:
        spam = SpamResult(
            is_spam=bool(data.get("is_spam")),
            confidence=float(
                data.get("spam_confidence", data.get("confidence", 0))
            ),
            reason=str(data.get("spam_reason") or data.get("reason") or ""),
        )

    dept_id = str(data.get("department_id") or "")
    dept_name = str(data.get("department_name") or "")
    dept_conf = float(data.get("dept_confidence", data.get("confidence", 0)))

    if not dept_id and candidates:
        top = candidates[0]
        dept_id = str(top.get("department_id", ""))
        dept_name = str(top.get("department_name", ""))
        if dept_conf <= 0:
            dept_conf = 0.0

    routing = RoutingResult(
        department_id=dept_id,
        department_name=dept_name,
        confidence=dept_conf,
        reasoning=str(data.get("reasoning") or ""),
    )

    summary = str(data.get("summary_ru") or data.get("text") or "").strip()
    summary_ru = clamp_summary(
        summary,
        max_sentences=settings.summary_max_sentences,
        max_chars=settings.summary_max_chars,
    )

    from agent_pochta.routing.xml_builder import build_stub_xml_theme, normalize_xml_theme

    raw_theme = str(data.get("xml_theme") or data.get("theme") or "").strip()
    if raw_theme:
        xml_theme = normalize_xml_theme(raw_theme, subject=subject, combined_text=combined_text)
    else:
        xml_theme = build_stub_xml_theme(subject, combined_text)

    partner_name = normalize_partner_name(
        data.get("partner_name") or data.get("partner")
    )

    from agent_pochta.routing.process_type import resolve_process_type

    process_type = resolve_process_type(
        llm_process=str(data.get("process_type") or data.get("process") or ""),
        subject=subject,
        combined_text=combined_text,
        claim=claim or bool(data.get("claim")),
    )

    return IncomingEmailAnalysis(
        spam=spam,
        routing=routing,
        summary_ru=summary_ru,
        xml_theme=xml_theme,
        partner_name=partner_name,
        process_type=process_type,
    )
