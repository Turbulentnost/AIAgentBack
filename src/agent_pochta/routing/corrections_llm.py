"""LLM-извлечение keywords для routing_corrections (6–7 department-discriminative фраз)."""

from __future__ import annotations

import json
from typing import Any

from agent_pochta.routing.corrections import (
    _clean_token,
    _dedupe_substring_keywords,
    _is_useful_keyword,
    _recipient_local_part,
    _strip_subject_prefix,
    extract_correction_keywords,
)
from agent_pochta.routing.normalize import normalize_text
from agent_pochta.services.routing_departments import load_routing_rules

KEYWORD_TARGET_MIN = 6
KEYWORD_TARGET_MAX = 7
_BODY_SNIPPET_LEN = 1500

# Few-shot примеры для итерации промпта (фаза 5): добавляйте удачные наборы после ручной проверки.
FEW_SHOT_EXAMPLES: list[dict[str, object]] = [
    {
        "subject": "Re: Заказ 12345 на расходомеры",
        "target_department": "ОМТО",
        "keywords": [
            "заказ 12345 на расходомеры",
            "uk_omto4",
            "согласовать спецификацию расходомера",
            "поставка расходомеров turbo-f",
            "коммерческое предложение расходомер",
            "срок поставки оборудования",
        ],
    },
    {
        "subject": "Акт сверки за квартал",
        "target_department": "Бухгалтерия",
        "keywords": [
            "акт сверки за квартал",
            "buh",
            "подписать акт сверки",
            "взаиморасчеты с контрагентом",
            "квартальная сверка задолженности",
            "закрывающие документы бухгалтерия",
        ],
    },
]

SYSTEM_PROMPT = """Ты аналитик маршрутизации входящей почты НПО «Турбулентность-ДОН».
Из текста письма извлеки keywords для базы знаний RAG: короткие фразы, по которым
однозначно понятно, что письмо относится к указанному ЦЕЛЕВОМУ отделу.

Правила:
- Верни JSON: {"keywords": ["...", ...]} — ровно 6 или 7 фраз, lowercase.
- Обязательно: 1 фраза из темы (смысловая, не только номер), local-part получателя (как передан),
  4–5 фраз из тела, объясняющих именно целевой отдел.
- Фразы 2–6 слов предпочтительнее одиночных слов; допускаются номера заказов/договоров в контексте.
- Запрещено: turbo, письмо, добрый день, здравствуйте, уважаемый, http, email, телефон,
  подпись, дубликаты, когда одна фраза полностью содержится в другой.
- Если отдел изменён оператором — keywords должны отражать признаки ЦЕЛЕВОГО отдела, не исходного.
- Не выдумывай факты, которых нет в тексте."""


def routing_rules_context(
    *,
    department_id: str | None,
    recipient: str | None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """2–3 правила из routing_rules.json для контекста промпта."""
    rules = load_routing_rules()
    picked: list[dict[str, Any]] = []
    local = _recipient_local_part(recipient) or ""

    for rule in rules.get("content_rules") or []:
        if department_id and str(rule.get("code") or "") == department_id:
            picked.append(
                {
                    "type": "content_rule",
                    "name": rule.get("name"),
                    "keywords": (rule.get("keywords") or [])[:8],
                    "about": rule.get("about"),
                }
            )
            if len(picked) >= limit:
                return picked[:limit]

    for rule in rules.get("email_keyword_rules") or []:
        keyword = str(rule.get("keyword") or "").lower()
        if department_id and str(rule.get("code") or "") == department_id:
            picked.append(
                {
                    "type": "email_keyword_rule",
                    "keyword": keyword,
                    "name": rule.get("name"),
                }
            )
        elif local and keyword == local:
            picked.append(
                {
                    "type": "recipient_keyword",
                    "keyword": keyword,
                    "name": rule.get("name"),
                    "code": rule.get("code"),
                }
            )
        if len(picked) >= limit:
            break

    return picked[:limit]


def build_keyword_extraction_user_payload(
    *,
    subject: str,
    body: str,
    sender_email: str,
    recipient: str | None,
    department_id: str,
    department_name: str,
    original_department_id: str | None,
    original_department_name: str | None,
    current_keywords: list[str] | None,
) -> str:
    subject_clean = _strip_subject_prefix(subject or "")
    body_snip = (body or "")[:_BODY_SNIPPET_LEN]
    payload = {
        "sender_email": sender_email,
        "recipient": recipient,
        "subject": subject_clean,
        "body_excerpt": body_snip,
        "target_department": {
            "department_id": department_id,
            "department_name": department_name,
        },
        "original_department": {
            "department_id": original_department_id or "",
            "department_name": original_department_name or "",
            "changed": bool(
                original_department_id
                and original_department_id != department_id
            ),
        },
        "routing_rules_hint": routing_rules_context(
            department_id=department_id,
            recipient=recipient,
        ),
        "few_shot_examples": FEW_SHOT_EXAMPLES,
        "current_keywords": current_keywords or [],
    }
    return json.dumps(payload, ensure_ascii=False)


def finalize_llm_keywords(
    raw_keywords: list[str],
    *,
    subject: str,
    recipient: str | None,
    department_id: str | None = None,
    corpus_entries: list[dict] | None = None,
) -> list[str]:
    """Пост-обработка: фильтр junk, force subject + local-part, dedupe, 6–7 items."""
    subject_clean = _strip_subject_prefix(subject or "")
    subject_norm = normalize_text(_clean_token(subject_clean))
    local_part = _recipient_local_part(recipient)

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_keywords:
        token = normalize_text(_clean_token(str(item).strip().lower()))
        if not token or token in seen or not _is_useful_keyword(token):
            continue
        seen.add(token)
        cleaned.append(token)

    result: list[str] = []
    if subject_norm and len(subject_norm) >= 4:
        result.append(subject_norm)
    if local_part:
        result.append(local_part)

    for token in cleaned:
        if token not in result:
            result.append(token)

    result = _dedupe_substring_keywords(result)

    if len(result) < KEYWORD_TARGET_MIN:
        fallback = extract_correction_keywords(
            subject,
            "",
            recipient=recipient,
            department_id=department_id,
            corpus_entries=corpus_entries,
        )
        for token in fallback:
            if token not in result:
                result.append(token)
        result = _dedupe_substring_keywords(result)

    if len(result) > KEYWORD_TARGET_MAX:
        # subject + local-part first, then longest phrases
        head = result[:2] if local_part and subject_norm else result[:1]
        tail = sorted(
            [k for k in result if k not in head],
            key=lambda k: (-len(k), k),
        )
        result = _dedupe_substring_keywords(head + tail)[:KEYWORD_TARGET_MAX]

    while len(result) < KEYWORD_TARGET_MIN and subject_norm and subject_norm not in result:
        result.insert(0, subject_norm)
        result = _dedupe_substring_keywords(result)

    return result[:KEYWORD_TARGET_MAX]


def extract_correction_keywords_llm(
    subject: str,
    body: str,
    *,
    sender_email: str,
    recipient: str | None,
    department_id: str,
    department_name: str,
    original_department_id: str | None = None,
    original_department_name: str | None = None,
    current_keywords: list[str] | None = None,
    corpus_entries: list[dict] | None = None,
) -> tuple[list[str], str]:
    """LLM keywords с fallback на extract_correction_keywords. Возвращает (keywords, source)."""
    from agent_pochta.config import get_settings
    from agent_pochta.services import build_container
    from agent_pochta.services.http_llm import ChatCompletionsLLMGateway
    from agent_pochta.services.gigachat_llm import GigaChatLLMGateway

    settings = get_settings()
    llm = build_container(settings).llm

    if not isinstance(llm, (ChatCompletionsLLMGateway, GigaChatLLMGateway)):
        keywords = extract_correction_keywords(
            subject,
            body,
            recipient=recipient,
            department_id=department_id,
            corpus_entries=corpus_entries,
        )
        return finalize_llm_keywords(
            keywords,
            subject=subject,
            recipient=recipient,
            department_id=department_id,
            corpus_entries=corpus_entries,
        ), "deterministic_fallback"

    user = build_keyword_extraction_user_payload(
        subject=subject,
        body=body,
        sender_email=sender_email,
        recipient=recipient,
        department_id=department_id,
        department_name=department_name,
        original_department_id=original_department_id,
        original_department_name=original_department_name,
        current_keywords=current_keywords,
    )

    try:
        data = llm._chat_json(SYSTEM_PROMPT, user)
        raw = data.get("keywords") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raise ValueError("LLM response missing keywords list")
        keywords = finalize_llm_keywords(
            [str(x) for x in raw],
            subject=subject,
            recipient=recipient,
            department_id=department_id,
            corpus_entries=corpus_entries,
        )
        return keywords, "llm"
    except Exception:
        keywords = extract_correction_keywords(
            subject,
            body,
            recipient=recipient,
            department_id=department_id,
            corpus_entries=corpus_entries,
        )
        return finalize_llm_keywords(
            keywords,
            subject=subject,
            recipient=recipient,
            department_id=department_id,
            corpus_entries=corpus_entries,
        ), "deterministic_fallback"
