"""Единый LLM-промпт: спам + отдел + обзор (1 API-вызов на письмо)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from agent_pochta.config import Settings, get_settings
from agent_pochta.rules.spam_context import analyze_spam_context, build_spam_llm_messages
from agent_pochta.schemas import EmailMessage, RoutingResult, SenderIdentity, SpamResult
from agent_pochta.services.summary import (
    build_summary_context,
    clamp_summary,
    extract_partner_from_signature,
    summary_ru_system_rules,
)

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
        "example.com",
        "example.org",
        "example.net",
    }
)
_ORG_LEGAL_PREFIX_RE = re.compile(
    r"(?<!\w)(?:"
    r"ООО|OOO|АО|AO|ПАО|PAO|ЗАО|ZAO|ИП|"
    r"ФГУП|ГУП|МУП|НКО|АНО|ЧОУ|ОАО|"
    r"LLC|Ltd\.?|Inc\.?|Corp\.?"
    r")(?!\w)",
    re.IGNORECASE,
)
_PERSON_FIO_RE = re.compile(
    r"^(?:"
    r"[А-ЯЁA-Z][а-яёa-z]+(?:\s+[А-ЯЁA-Z]\.?)?(?:\s+[А-ЯЁA-Z][а-яёa-z]+)?"
    r"|[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}"
    r")\.?$"
)
_GENERIC_MAILBOX_LABELS = frozenset({"mail", "info", "support", "noreply", "no-reply"})
_JOB_TITLE_RE = re.compile(
    r"(?i)(?:"
    r"инженер|менеджер|директор|специалист|руководитель|начальник|"
    r"бухгалтер|юрист|секретарь|ассистент|консультант|эксперт|"
    r"категории|должност|ведущий\s+|старший\s+"
    r")"
)
_SUMMARY_COMPANY_RE = re.compile(
    r"(?:"
    r"(?:от\s+)?комп(?:ании|ания)\s+[«\"*]*([^»\"*.,;!\n]+?)[»\"*]*"
    r"(?=\s+(?:отвечает|просит|запрашивает|направляет|сообщает|спрашивает|пишет|уточняет)"
    r"|[.,;!\n]|$)"
    r"|"
    r"(?:из|от)\s+("
    r"(?:ООО|OOO|АО|AO|ПАО|PAO|ЗАО|ZAO|ИП|ФГУП|ГУП|МУП|НКО|АНО|ЧОУ|ОАО)"
    r"[\s«\"]*[^\n,;.!?\s][^\n,;.!?]{0,79}?)"
    r"(?=[»\"]|[.,;!?\n]|\s+[а-яё]|$)"
    r")",
    re.IGNORECASE,
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


def lookup_contractor_name(query: str, *, qdrant_url: str | None) -> str | None:
    """Ищет каноническое имя контрагента в Qdrant по фрагменту из подписи."""
    normalized = normalize_partner_name(query)
    if not normalized or not qdrant_url:
        return None

    from agent_pochta.services.rag_qdrant import search_contractors

    search_queries = [normalized]
    if "«" in normalized:
        inner = normalized.split("«", 1)[-1].split("»", 1)[0].strip()
        if inner and inner not in search_queries:
            search_queries.append(inner)

    for search_query in search_queries:
        hits = search_contractors(qdrant_url, search_query, limit=5)
        if not hits:
            continue
        query_l = search_query.lower()
        for hit in hits:
            name = normalize_partner_name(str(hit.get("name") or ""))
            if not name:
                continue
            name_l = name.lower()
            if query_l in name_l or name_l in query_l:
                return name
        first_name = normalize_partner_name(str(hits[0].get("name") or ""))
        if first_name:
            return first_name
    return None


def looks_like_job_title(name: str | None) -> bool:
    """True для должности/роли («Инженер 1 категории…»), а не названия компании."""
    value = (name or "").strip()
    if not value or _ORG_LEGAL_PREFIX_RE.search(value):
        return False
    return bool(_JOB_TITLE_RE.search(value))


def looks_like_org_name(name: str | None) -> bool:
    """True, если строка похожа на организацию, а не на ФИО/должность."""
    value = (name or "").strip()
    if not value or value.lower() in _EMPTY_PARTNER_MARKERS:
        return False
    if _ORG_LEGAL_PREFIX_RE.search(value):
        return True
    if looks_like_job_title(value) or looks_like_person_name(value):
        return False
    return True


def looks_like_person_name(name: str | None) -> bool:
    """True для ФИО (кириллица/латиница) без маркеров юрлица."""
    value = (name or "").strip()
    if not value or _ORG_LEGAL_PREFIX_RE.search(value):
        return False
    return bool(_PERSON_FIO_RE.match(value))


def _partner_core_key(name: str | None) -> str:
    """Ядро названия без правовой формы и кавычек для сравнения сущностей."""
    value = normalize_partner_name(name) or ""
    value = _ORG_LEGAL_PREFIX_RE.sub(" ", value)
    value = re.sub(r"[«»\"'`]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return re.sub(r"[\s.\-]+", "", value)


def partners_same_entity(left: str | None, right: str | None) -> bool:
    """True, если оба имени относятся к одной организации (с учётом ООО/кавычек)."""
    a = _partner_core_key(left)
    b = _partner_core_key(right)
    if not a or not b:
        return False
    return a in b or b in a


def _format_domain_label(label: str) -> str:
    parts = [part for part in label.split("-") if part]
    if not parts:
        return ""
    titled = [part.title() for part in parts]
    if len(titled) >= 2 and max(len(part) for part in parts) <= 6:
        return "-".join(titled)
    return " ".join(titled)


def infer_partner_from_domain(sender_email: str) -> str | None:
    """Название компании из корпоративного домена (h-energy.ru → H-Energy)."""
    email = (sender_email or "").strip().lower()
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip()
    if not domain or domain in _FREE_EMAIL_DOMAINS:
        return None
    label = domain.split(".", 1)[0]
    if not label or label in _GENERIC_MAILBOX_LABELS:
        return None
    return normalize_partner_name(_format_domain_label(label))


def extract_partner_from_summary(summary_ru: str | None) -> str | None:
    """Извлекает компанию из обзора («от компании…», «из ООО …»)."""
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", (summary_ru or "").strip())
    if not text:
        return None
    match = _SUMMARY_COMPANY_RE.search(text)
    if not match:
        return None
    raw = next((g for g in match.groups() if g), None)
    candidate = normalize_partner_name(raw)
    if candidate and looks_like_org_name(candidate):
        return candidate
    return None


def infer_partner_from_email(email: EmailMessage) -> str | None:
    """Эвристика: домен корпоративной почты, затем org-like From (не ФИО)."""
    domain_partner = infer_partner_from_domain(email.sender_email)
    if domain_partner:
        return domain_partner

    sender_name = (email.sender_name or "").strip()
    if (
        sender_name
        and "@" not in sender_name
        and len(sender_name) >= 3
        and sender_name.lower() not in _EMPTY_PARTNER_MARKERS
        and looks_like_org_name(sender_name)
    ):
        return normalize_partner_name(sender_name)
    return None


def _canonicalize_partner(partner: str | None, *, qdrant_url: str | None) -> str | None:
    normalized = normalize_partner_name(partner)
    if not normalized:
        return None
    canonical = lookup_contractor_name(normalized, qdrant_url=qdrant_url)
    return canonical or normalized


def resolve_partner_name(
    *,
    llm_partner: str | None,
    rag_partner: str | None,
    email: EmailMessage | None = None,
    body_text: str | None = None,
    summary_ru: str | None = None,
    qdrant_url: str | None = None,
    learned_partner: str | None = None,
) -> str | None:
    """HITL → подпись → LLM/обзор (согласованность) → домен → RAG → From (org)."""
    learned = normalize_partner_name(learned_partner)
    if learned:
        return _canonicalize_partner(learned, qdrant_url=qdrant_url)

    text = body_text if body_text is not None else (email.body_text if email else "")

    signature_partner = extract_partner_from_signature(text or "")
    if signature_partner:
        return _canonicalize_partner(signature_partner, qdrant_url=qdrant_url)

    summary_partner = extract_partner_from_summary(summary_ru)
    llm = normalize_partner_name(llm_partner)
    if llm and looks_like_org_name(llm):
        # Компания в summary_ru и partner_name — одна сущность; иначе доверяем обзору.
        if summary_partner and not partners_same_entity(llm, summary_partner):
            return _canonicalize_partner(summary_partner, qdrant_url=qdrant_url)
        return _canonicalize_partner(llm, qdrant_url=qdrant_url)

    if email is not None:
        domain_partner = infer_partner_from_domain(email.sender_email)
        if domain_partner:
            if summary_partner and not partners_same_entity(domain_partner, summary_partner):
                return _canonicalize_partner(summary_partner, qdrant_url=qdrant_url)
            return _canonicalize_partner(domain_partner, qdrant_url=qdrant_url)

    if summary_partner:
        return _canonicalize_partner(summary_partner, qdrant_url=qdrant_url)

    rag = normalize_partner_name(rag_partner)
    if rag and looks_like_org_name(rag):
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
        "Ты внутренний классификатор входящей почты ООО НПО «Турбулентность-ДОН». "
        "Аудитория — офис-менеджер и 1С, не автор письма. Один ответ, только JSON. "
        "Не веди диалог с отправителем.\n\n"
        "Вход: subject, body_and_attachments, attachments_text, email_signature, candidates. "
        "Учитывай вложения (счета, акты, договоры, претензии) для partner_name, отдела, "
        "summary_ru и xml_theme — даже при коротком теле.\n\n"
        f"{spam_block}"
        "2) Отдел: ровно один department_id из candidates "
        "(кандидаты из allowlist служб). dept_confidence 0..1, reasoning.\n"
        f"3) {summary_ru_system_rules(min_sent=min_sent, max_sent=settings.summary_max_sentences)}\n"
        "4) xml_theme (≤200 символов, русский) — действие, требуемое в письме: "
        "формат «Глагол: краткая тема». "
        "Глагол из: Запрос, Проверить, Решить, Согласовать, Оплатить, Ознакомиться, Рассмотреть. "
        "Не копируй тело; не ставь шаблонное «Действие»; убери Re:/Fw:/Fwd:.\n"
        "5) partner_name — компания (юрлицо/бренд), от которой пришёл запрос "
        "(поле «Партнёр» в 1С, ≤200 символов). Не человек и не должность.\n"
        "   Бери юрлицо/бренд; предпочтительнее русское наименование с правовой формой.\n"
        "   Запрещено: ФИО; должность/роль; чужое подразделение без названия компании; "
        "мы (НПО/ООО «Турбулентность-ДОН»); голая форма («ООО») без имени.\n"
        "   Если в summary_ru названа компания — partner_name должен быть той же.\n"
        "   Источники по приоритету: email_signature → attachments_text → тело письма. "
        "Не бери домен/From, если организация есть в тексте. "
        "contractor_name — только подсказка.\n"
        "   Контрпример: «Инженер 1 категории…, БелГИМ» → partner_name «БелГИМ».\n"
        "   Неизвестно → \"\".\n"
        "6) process_type — по СУТИ требуемого действия, не по типу вложения.\n"
        "   Сначала сформулируй xml_theme (п.4), затем process_type должен совпадать с его глаголом:\n"
        "   • Ознакомиться → ознакомление\n"
        "   • Рассмотреть / Решить / Согласовать / Проверить → рассмотрение\n"
        "   • Запрос / Оплатить → исполнение (только если от нас явно требуют конкретную операцию)\n"
        "   ознакомление — информирование без ответа/поручения: уведомления, статусы, FYI, "
        "«для сведения», «сообщаем/информируем», сроки отгрузки без просьбы что-то сделать.\n"
        "   рассмотрение — нужна оценка или решение: претензии, ТКП/КП, договор на согласование, "
        "входящие счета/акты/УПД «на проверку» без «выставьте/направьте/оплатите».\n"
        "   исполнение — явное поручение выполнить: «просим выставить счёт», «направьте документы», "
        "«оплатите», «выполните заказ», «пришлите по ЭДО», «предоставьте» со сроком или без.\n"
        "   НЕ ставь исполнение по умолчанию: наличие счёта/акта/договора во вложении ≠ исполнение.\n"
        "7) Приоритет G.1 (итоговый приоритет ставит агент; учти при выборе отдела):\n"
        "   • госорганы/суды/надзор → директор (00-000152);\n"
        "   • претензии/требования → юрист (00-000044);\n"
        "   • УПД/счета/акты без срока → бухгалтерия, низкий;\n"
        "   • срок ответа / требование / поручение → 1-я очередь.\n\n"
        "Ответ строго JSON:\n"
        "{"
        f"{spam_json_fields}"
        '"department_id": "...", "department_name": "...", '
        '"dept_confidence": float, "reasoning": "...", "summary_ru": "...", '
        '"xml_theme": "Действие требуемое в письме: краткая тема", '
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
        is_spam = bool(data.get("is_spam"))
        if "spam_confidence" in data:
            spam_conf = float(data.get("spam_confidence") or 0)
        elif "dept_confidence" in data:
            # Ответ analyze_incoming без spam_confidence → дефолт по is_spam.
            spam_conf = 0.9 if is_spam else 0.05
        else:
            spam_conf = float(data.get("confidence", 0.9 if is_spam else 0.05) or 0)
        spam = SpamResult(
            is_spam=is_spam,
            confidence=spam_conf,
            reason=str(data.get("spam_reason") or data.get("reason") or ""),
        )

    dept_id = str(data.get("department_id") or "")
    dept_name = str(data.get("department_name") or "")
    if "dept_confidence" in data:
        dept_conf = float(data.get("dept_confidence") or 0)
    elif "spam_confidence" in data:
        # Не подставлять spam_confidence/общий confidence в уверенность отдела.
        dept_conf = 0.0
    else:
        dept_conf = float(data.get("confidence", 0) or 0)

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

    from agent_pochta.routing.xml_builder import build_subject_xml_theme, normalize_xml_theme

    raw_theme = str(data.get("xml_theme") or data.get("theme") or "").strip()

    from agent_pochta.routing.process_type import resolve_process_type

    process_type = resolve_process_type(
        llm_process=str(data.get("process_type") or data.get("process") or ""),
        subject=subject,
        combined_text=combined_text,
        claim=claim or bool(data.get("claim")),
    )

    if raw_theme:
        xml_theme = normalize_xml_theme(
            raw_theme,
            subject=subject,
            combined_text=combined_text,
            process_type=process_type,
            claim=claim or bool(data.get("claim")),
        )
    else:
        xml_theme = build_subject_xml_theme(
            subject,
            combined_text=combined_text,
            process_type=process_type,
            claim=claim or bool(data.get("claim")),
        )

    partner_name = normalize_partner_name(
        data.get("partner_name") or data.get("partner")
    )

    return IncomingEmailAnalysis(
        spam=spam,
        routing=routing,
        summary_ru=summary_ru,
        xml_theme=xml_theme,
        partner_name=partner_name,
        process_type=process_type,
    )
