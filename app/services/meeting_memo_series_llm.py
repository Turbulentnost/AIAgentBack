"""LLM-извлечение серии совещаний из СЗ при обнаружении периодичности в тексте."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.builder.llm import parse_json_content
from app.agents.meeting_agent.prompts.memo_series_planning_prompt import (
    MEMO_SERIES_PLANNING_PROMPT_VERSION,
    MEMO_SERIES_PLANNING_SYSTEM_PROMPT,
)
from app.core.config import settings
from app.llm.errors import format_llm_call_error
from app.llm.gateway import llm_gateway
from app.models.enums import ScheduledMeetingFrequency, ScheduledMeetingWeekday
from app.services.meeting_duration import DEFAULT_MEETING_DURATION_MINUTES
from app.services.meeting_memo_document import clean_text, parse_odata_date
from app.services.meeting_memo_recurrence import (
    MemoRecurrenceDraft,
    _finalize_recurrence_draft,
    _find_source_quote,
    _resolve_from_schedule,
    _time_from_header,
    build_series_planning_read,
    collect_recurrence_texts,
    has_recurrence_hints,
    resolve_memo_recurrence_rules,
    resolve_source_quote,
)
from app.services.meeting_memo_series_calendar import format_series_calendar_context
from app.services.meeting_redis_ops import meeting_redis_get, meeting_redis_setex
from app.services.scheduled_meeting_recurrence import (
    RecurrenceInput,
    default_series_end_date,
    validate_recurrence_input,
)

_THINK_BLOCK_RE = re.compile(
    r"<think>.*?</think>|<thinking>.*?</thinking>",
    flags=re.DOTALL | re.IGNORECASE,
)

logger = logging.getLogger(__name__)

LLMChatFn = Callable[..., Awaitable[dict[str, Any]]]

ConfidenceLevel = Literal["high", "medium", "low"]
WeekdayLiteral = Literal[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]
FrequencyLiteral = Literal["daily", "weekly", "monthly", "yearly"]


class MemoSeriesLLMResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    is_series: bool = False
    confidence: ConfidenceLevel = "medium"
    frequency: FrequencyLiteral | None = None
    interval: int = Field(default=1, ge=1)
    weekday: WeekdayLiteral | None = None
    time_local: str | None = Field(default=None, description="HH:MM")
    duration_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    series_start_date: str | None = Field(default=None, description="YYYY-MM-DD")
    series_end_date: str | None = Field(default=None, description="YYYY-MM-DD")
    source_quote: str | None = None
    ambiguities: list[str] = Field(default_factory=list)
    reasoning: str | None = None


_WEEKDAY_FROM_LLM: dict[str, ScheduledMeetingWeekday] = {
    "monday": ScheduledMeetingWeekday.MONDAY,
    "tuesday": ScheduledMeetingWeekday.TUESDAY,
    "wednesday": ScheduledMeetingWeekday.WEDNESDAY,
    "thursday": ScheduledMeetingWeekday.THURSDAY,
    "friday": ScheduledMeetingWeekday.FRIDAY,
    "saturday": ScheduledMeetingWeekday.SATURDAY,
    "sunday": ScheduledMeetingWeekday.SUNDAY,
}

_FREQ_FROM_LLM: dict[str, ScheduledMeetingFrequency] = {
    "daily": ScheduledMeetingFrequency.DAILY,
    "weekly": ScheduledMeetingFrequency.WEEKLY,
    "monthly": ScheduledMeetingFrequency.MONTHLY,
    "yearly": ScheduledMeetingFrequency.YEARLY,
}


def _series_llm_cache_key(ref_key: str) -> str:
    return f"meeting:memo:{ref_key.strip().lower()}:series_llm"


def _memo_series_fingerprint(header: dict[str, Any]) -> str:
    texts = collect_recurrence_texts(header)
    payload = json.dumps(
        {"prompt_version": MEMO_SERIES_PLANNING_PROMPT_VERSION, "texts": texts},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _parse_iso_date(value: str | None) -> date | None:
    if not value or not str(value).strip():
        return None
    normalized = str(value).strip()[:10]
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return parse_odata_date(value)


def _parse_hh_mm(value: str | None) -> time | None:
    if not value or not str(value).strip():
        return None
    text = str(value).strip().replace(".", ":")
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def _series_start_from_context(header: dict[str, Any]) -> date | None:
    for key in (
        "ЖелаемаяДатаПроведенияСовещания",
        "ДатаПроведенияСовещания",
        "Date",
        "Дата",
    ):
        parsed = parse_odata_date(header.get(key))
        if parsed is not None:
            return parsed
    return None


def _build_llm_user_prompt(header: dict[str, Any], document: dict[str, Any] | None) -> str:
    app = (document or {}).get("application") if isinstance(document, dict) else None
    app = app if isinstance(app, dict) else {}

    lines = [
        "Проанализируй служебную записку и верни JSON по шаблону.",
        "",
        f"Номер СЗ: {clean_text(header.get('Number')) or '—'}",
        f"Дата документа: {clean_text(header.get('Date')) or clean_text(app.get('document_date')) or '—'}",
        f"Желаемая дата совещания: {clean_text(header.get('ЖелаемаяДатаПроведенияСовещания')) or '—'}",
        f"Время начала (шапка): {clean_text(header.get('ВремяНачалаСовещания')) or clean_text(app.get('meeting_start')) or '—'}",
        f"Время окончания (шапка): {clean_text(header.get('ВремяОкончанияСовещания')) or clean_text(app.get('meeting_end')) or '—'}",
        f"Тема: {clean_text(header.get('ТемаСлужебнойЗаписки')) or clean_text(header.get('ТемаСовещания')) or '—'}",
        "",
        "Текст / повестка:",
        clean_text(header.get("ТекстСлужебнойЗаписки"))
        or clean_text(app.get("agenda"))
        or clean_text(header.get("Комментарий"))
        or "—",
    ]
    anchor = _series_start_from_context(header)
    if anchor is not None:
        lines.extend(["", format_series_calendar_context(anchor)])
    return "\n".join(lines)


def _llm_response_to_recurrence(
    response: MemoSeriesLLMResponse,
    header: dict[str, Any],
) -> tuple[RecurrenceInput | None, list[str]]:
    """Переводит ответ LLM (даты + периодичность) в RecurrenceInput для расчёта в коде."""
    ambiguities = list(response.ambiguities)
    if not response.is_series:
        return None, ambiguities

    frequency = _FREQ_FROM_LLM.get(response.frequency or "")
    if frequency is None:
        ambiguities.append("LLM не указала frequency серии")
        return None, ambiguities

    series_start = _parse_iso_date(response.series_start_date) or _series_start_from_context(header)
    if series_start is None:
        ambiguities.append("Не указана дата начала серии")
        return None, ambiguities

    series_end = _parse_iso_date(response.series_end_date) or default_series_end_date(
        year=series_start.year
    )

    time_local = _parse_hh_mm(response.time_local)
    header_time, header_duration = _time_from_header(header)
    if time_local is None:
        time_local = header_time
    if time_local is None:
        ambiguities.append("Не указано время начала серии")

    duration_minutes = response.duration_minutes or header_duration or DEFAULT_MEETING_DURATION_MINUTES

    weekday = _WEEKDAY_FROM_LLM.get(response.weekday or "") if response.weekday else None
    if frequency == ScheduledMeetingFrequency.WEEKLY and weekday is None and series_start:
        from app.services.meeting_memo_recurrence import _ISO_TO_WEEKDAY

        weekday = _ISO_TO_WEEKDAY.get(series_start.weekday())

    if time_local is None:
        return None, ambiguities

    recurrence = RecurrenceInput(
        frequency=frequency,
        interval=max(1, response.interval),
        time_local=time_local,
        duration_minutes=duration_minutes,
        series_start_date=series_start,
        series_end_date=series_end,
        weekday=weekday if frequency == ScheduledMeetingFrequency.WEEKLY else None,
    )
    try:
        validate_recurrence_input(recurrence)
    except ValueError as exc:
        ambiguities.append(str(exc))
        return None, ambiguities
    return recurrence, ambiguities


def draft_from_llm_response(
    response: MemoSeriesLLMResponse,
    header: dict[str, Any],
) -> MemoRecurrenceDraft:
    if not response.is_series:
        return MemoRecurrenceDraft(
            is_series=False,
            confidence="high",
            planning_options=["single"],
        )

    recurrence, ambiguities = _llm_response_to_recurrence(response, header)
    if recurrence is None:
        confidence: ConfidenceLevel = response.confidence
        if confidence == "high":
            confidence = "low"
        return MemoRecurrenceDraft(
            is_series=True,
            confidence=confidence,
            source_quote=response.source_quote,
            ambiguities=ambiguities or list(response.ambiguities),
            planning_options=["single"],
        )

    draft = _finalize_recurrence_draft(
        recurrence,
        ambiguities,
        source_quote=response.source_quote or resolve_source_quote(
            header,
            " ".join(collect_recurrence_texts(header)),
        ),
    )
    if response.confidence == "low":
        draft.confidence = "low"
        draft.requires_user_choice = False
        draft.planning_options = ["single"]
    elif response.confidence == "medium" and draft.confidence == "high":
        draft.confidence = "medium"
    return draft


async def _read_llm_cache(ref_key: str, fingerprint: str) -> MemoSeriesLLMResponse | None:
    try:
        raw = await meeting_redis_get(_series_llm_cache_key(ref_key))
    except Exception as exc:
        logger.warning("meeting_memo_series_llm_cache_read_failed", error=str(exc))
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if payload.get("fingerprint") != fingerprint:
        return None
    try:
        return MemoSeriesLLMResponse.model_validate(payload.get("response") or {})
    except ValidationError:
        return None


async def _write_llm_cache(
    ref_key: str,
    fingerprint: str,
    response: MemoSeriesLLMResponse,
) -> None:
    try:
        await meeting_redis_setex(
            _series_llm_cache_key(ref_key),
            settings.MEETING_DASHBOARD_CACHE_TTL_SECONDS,
            json.dumps(
                {
                    "fingerprint": fingerprint,
                    "response": response.model_dump(mode="json"),
                    "cached_at": datetime.utcnow().isoformat(),
                },
                ensure_ascii=False,
            ),
        )
    except Exception as exc:
        logger.warning("meeting_memo_series_llm_cache_write_failed", error=str(exc))


def _series_llm_model() -> str:
    return (
        settings.MEETING_MEMO_SERIES_LLM_MODEL
        or settings.LLM_DEFAULT_MODEL
        or "openai/gpt-oss-120b"
    )


def _should_disable_thinking(model: str) -> bool:
    normalized = model.lower()
    return any(token in normalized for token in ("qwen", "nemotron", "gpt-oss", "deepseek-r1"))


def _coerce_message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _strip_thinking_blocks(text: str) -> str:
    cleaned = _THINK_BLOCK_RE.sub("", text)
    # Незакрытый think-блок в начале ответа reasoning-моделей.
    cleaned = re.sub(r"^<think>.*?(?:</think>|$)", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


def _extract_assistant_text(message: dict[str, Any]) -> str:
    """Собирает текст ответа; предпочитает фрагмент, где есть JSON-объект."""
    candidates: list[str] = []
    for key in ("content", "reasoning_content", "reasoning"):
        text = _strip_thinking_blocks(_coerce_message_text(message.get(key)))
        if text:
            candidates.append(text)

    if not candidates:
        return ""

    for text in candidates:
        if "{" in text and "}" in text:
            return text
    return "\n".join(candidates)


def _parse_series_llm_content(content: str) -> MemoSeriesLLMResponse:
    parsed = parse_json_content(content)
    return MemoSeriesLLMResponse.model_validate(parsed)


def _build_memo_series_messages(
    header: dict[str, Any],
    document: dict[str, Any] | None,
    *,
    model: str,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": MEMO_SERIES_PLANNING_SYSTEM_PROMPT},
        {"role": "user", "content": _build_llm_user_prompt(header, document)},
    ]
    if _should_disable_thinking(model):
        messages.append(
            {
                "role": "assistant",
                "content": "/no_think\nОтвечу только валидным JSON без markdown.\n",
            }
        )
    return messages


async def _chat_series_llm(
    chat: LLMChatFn,
    messages: list[dict[str, str]],
    *,
    model: str,
) -> dict[str, Any]:
    max_tokens = settings.MEETING_MEMO_SERIES_LLM_MAX_TOKENS
    if _should_disable_thinking(model):
        # Reasoning-модели часто сжигают бюджет на «мысли» — даём запас под JSON.
        max_tokens = max(max_tokens, 2500)
    try:
        return await chat(
            messages,
            model=model,
            temperature=0.1,
            max_tokens=max_tokens,
            timeout=120,
        )
    except Exception as exc:
        raise RuntimeError(format_llm_call_error(exc)) from exc


def _message_from_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("choices", [{}])[0].get("message", {})
    if not isinstance(message, dict):
        raise RuntimeError("LLM вернула некорректный ответ")
    return message


async def call_memo_series_llm(
    header: dict[str, Any],
    document: dict[str, Any] | None,
    *,
    llm_chat: LLMChatFn | None = None,
) -> MemoSeriesLLMResponse:
    chat = llm_chat or llm_gateway.chat
    model = _series_llm_model()
    messages = _build_memo_series_messages(header, document, model=model)
    payload = await _chat_series_llm(chat, messages, model=model)
    message = _message_from_chat_payload(payload)

    content = _extract_assistant_text(message)
    if not content:
        raise RuntimeError("LLM вернула пустой ответ")

    try:
        return _parse_series_llm_content(content)
    except (json.JSONDecodeError, ValidationError) as first_exc:
        logger.warning(
            "meeting_memo_series_llm_json_retry: %s; preview=%r",
            first_exc,
            content[:300],
        )
        repair_messages = [
            *messages,
            {"role": "assistant", "content": content[:2000]},
            {
                "role": "user",
                "content": (
                    "Ответ выше нельзя разобрать как JSON. "
                    "Верни ТОЛЬКО один JSON-объект по шаблону из system prompt, "
                    "без markdown и без пояснений."
                ),
            },
        ]
        if _should_disable_thinking(model):
            repair_messages.append(
                {"role": "assistant", "content": "/no_think\n{\"is_series\":"}
            )
        try:
            repair_payload = await _chat_series_llm(chat, repair_messages, model=model)
            repair_content = _extract_assistant_text(_message_from_chat_payload(repair_payload))
            if not repair_content:
                raise RuntimeError("LLM вернула пустой ответ при повторе")
            return _parse_series_llm_content(repair_content)
        except (json.JSONDecodeError, ValidationError, RuntimeError) as retry_exc:
            raise RuntimeError(
                f"Не удалось разобрать JSON от LLM: {first_exc}"
            ) from retry_exc


async def resolve_memo_recurrence_async(
    header: dict[str, Any],
    document: dict[str, Any] | None = None,
    *,
    ref_key: str | None = None,
    llm_chat: LLMChatFn | None = None,
) -> MemoRecurrenceDraft:
    """Расписание 1С — правила; намёк в тексте — LLM; иначе единоразовое."""
    schedule_draft = _resolve_from_schedule(header)
    if schedule_draft is not None:
        return schedule_draft

    texts = collect_recurrence_texts(header)
    if not has_recurrence_hints(texts):
        return MemoRecurrenceDraft(
            is_series=False,
            confidence="high",
            planning_options=["single"],
        )

    normalized_ref = (ref_key or clean_text(header.get("Ref_Key")) or "").strip().lower()
    fingerprint = _memo_series_fingerprint(header)

    if normalized_ref:
        cached = await _read_llm_cache(normalized_ref, fingerprint)
        if cached is not None:
            return draft_from_llm_response(cached, header)

    if not settings.MEETING_MEMO_SERIES_LLM_ENABLED:
        return resolve_memo_recurrence_rules(header, document)

    try:
        llm_response = await call_memo_series_llm(header, document, llm_chat=llm_chat)
        if normalized_ref:
            await _write_llm_cache(normalized_ref, fingerprint, llm_response)
        return draft_from_llm_response(llm_response, header)
    except Exception as exc:
        logger.warning("meeting_memo_series_llm_failed: %s", exc)
        rules_draft = resolve_memo_recurrence_rules(header, document)
        if rules_draft.recurrence is not None and "series" in rules_draft.planning_options:
            return rules_draft
        combined = " ".join(texts)
        ambiguities = [
            f"Не удалось рассчитать параметры серии: {exc}",
            *rules_draft.ambiguities,
        ]
        return MemoRecurrenceDraft(
            is_series=True,
            confidence="low",
            source_quote=rules_draft.source_quote or resolve_source_quote(header, combined),
            ambiguities=ambiguities,
            planning_options=["single"],
        )


async def build_series_planning_read_async(
    header: dict[str, Any],
    document: dict[str, Any] | None = None,
    *,
    ref_key: str | None = None,
    selected_mode: str | None = None,
    llm_chat: LLMChatFn | None = None,
) -> dict[str, Any]:
    draft = await resolve_memo_recurrence_async(
        header,
        document,
        ref_key=ref_key,
        llm_chat=llm_chat,
    )
    payload = build_series_planning_read(draft)
    payload["source"] = "llm" if has_recurrence_hints(collect_recurrence_texts(header)) else "rules"
    if selected_mode in {"series", "single"}:
        payload["selected_mode"] = selected_mode
    return payload
