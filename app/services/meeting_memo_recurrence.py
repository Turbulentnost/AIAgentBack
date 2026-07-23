"""Rule-based извлечение серии совещаний из текста служебной записки."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Any, Literal

from app.models.enums import ScheduledMeetingFrequency, ScheduledMeetingWeekday
from app.services.meeting_memo_document import (
    clean_text,
    extract_memo_text,
    looks_like_guid,
    parse_odata_date,
    parse_odata_time_component,
    resolve_meeting_schedule,
    schedule_duration_minutes,
)
from app.services.meeting_duration import DEFAULT_MEETING_DURATION_MINUTES
from app.services.scheduled_meeting_recurrence import (
    RecurrenceInput,
    WEEKDAY_TO_ISO,
    default_series_end_date,
    format_recurrence_label,
    iter_occurrence_dates,
    validate_recurrence_input,
)

ConfidenceLevel = Literal["high", "medium", "low"]
PlanningOption = Literal["series", "single"]

_RECURRENCE_HINT_RE = re.compile(
    r"ежеднев|кажд(?:ый|ую)\s+день|еженедел|кажд(?:ую|ый)\s+недел|"
    r"раз\s+в\s+\d+|раз\s+в\s+(?:две|три|четыре|пять|шесть|семь|восемь|девять|десять)\s+недел|"
    r"на\s+(?:\d+|одну|две|три|четыре|пять|шесть|семь|восемь|девять|десять)\s+недел|"
    r"на\s+(?:\d+|один|два|три|четыре|пять|шесть|семь|восемь|девять|десять)\s+дн|"
    r"повтор|серия\s+совещ",
    re.IGNORECASE,
)

_WEEKDAYS_ONLY_RE = re.compile(r"по\s+будням", re.IGNORECASE)

_TIME_RANGE_RE = re.compile(
    r"(?:"
    r"(?:с\s+)?(\d{1,2})[:.](\d{2})\s*(?:[-–—]|до)\s*(\d{1,2})[:.](\d{2})"
    r")",
    re.IGNORECASE,
)

_TIME_AT_RE = re.compile(
    r"\bв\s+(\d{1,2})[:.](\d{2})\b",
    re.IGNORECASE,
)

_END_DATE_RE = re.compile(
    r"(?:до|по)\s+"
    r"(?:"
    r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?"
    r"|"
    r"(\d{1,2})\s+(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*(?:\s+(\d{4}))?"
    r")",
    re.IGNORECASE,
)

_ON_WEEKS_RE = re.compile(
    r"на\s+(одну|две|три|четыре|пять|шесть|семь|восемь|девять|десять|\d+)\s+недел",
    re.IGNORECASE,
)

_BOUNDED_DURATION_RE = re.compile(
    r"на\s+(?:"
    r"вс[юу]|весь|вся|все|эту|это|текущ|данн"
    r"|\d+|одну|один|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять"
    r")\s+(?:недел|месяц|квартал|год|полгод)"
    r"|до\s+конца\s+(?:недел|месяц|квартал|года|полугод)"
    r"|(?:весь|вся|вс[юу])\s+(?:недел|месяц|квартал|год|полгод)"
    r"|квартал|полгод",
    re.IGNORECASE,
)

_ON_DAYS_RE = re.compile(
    r"на\s+(один|два|три|четыре|пять|шесть|семь|восемь|девять|десять|\d+)\s+дн",
    re.IGNORECASE,
)

_EVERY_N_DAYS_RE = re.compile(r"каждые\s+(\d+)\s+дн", re.IGNORECASE)

_WEEKLY_EVERY_N_RE = re.compile(
    r"раз\s+в\s+(две|три|четыре|пять|шесть|семь|восемь|девять|десять|\d+)\s+недел",
    re.IGNORECASE,
)

_WEEKDAY_IN_TEXT_RE = re.compile(
    r"по\s+(понедельник|вторник|сред|четверг|пятниц|суббот|воскресен)",
    re.IGNORECASE,
)

_NUMBER_WORDS: dict[str, int] = {
    "один": 1,
    "одну": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
}

_MONTHS: dict[str, int] = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "ма": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}

_WEEKDAY_FROM_TEXT: list[tuple[re.Pattern[str], ScheduledMeetingWeekday]] = [
    (re.compile(r"понедельник", re.I), ScheduledMeetingWeekday.MONDAY),
    (re.compile(r"вторник", re.I), ScheduledMeetingWeekday.TUESDAY),
    (re.compile(r"сред", re.I), ScheduledMeetingWeekday.WEDNESDAY),
    (re.compile(r"четверг", re.I), ScheduledMeetingWeekday.THURSDAY),
    (re.compile(r"пятниц", re.I), ScheduledMeetingWeekday.FRIDAY),
    (re.compile(r"суббот", re.I), ScheduledMeetingWeekday.SATURDAY),
    (re.compile(r"воскресен", re.I), ScheduledMeetingWeekday.SUNDAY),
]

_ISO_TO_WEEKDAY: dict[int, ScheduledMeetingWeekday] = {
    iso: weekday for weekday, iso in WEEKDAY_TO_ISO.items()
}

_TEXT_FIELD_KEYS = (
    "ТекстСлужебнойЗаписки",
    "ТемаСовещания",
    "ЦельПланаСовещания",
    "Комментарий",
)


@dataclass(slots=True)
class ParsedRecurrenceRules:
    frequency: ScheduledMeetingFrequency | None = None
    interval: int = 1
    weekday: ScheduledMeetingWeekday | None = None
    time_local: time | None = None
    duration_minutes: int | None = None
    series_start_date: date | None = None
    series_end_date: date | None = None
    end_after_days: int | None = None
    end_after_weeks: int | None = None
    source_quote: str | None = None
    ambiguities: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MemoRecurrenceDraft:
    is_series: bool = False
    confidence: ConfidenceLevel = "low"
    recurrence: RecurrenceInput | None = None
    recurrence_label: str | None = None
    occurrence_count: int | None = None
    series_start_date: date | None = None
    series_end_date: date | None = None
    series_end_label: str | None = None
    source_quote: str | None = None
    ambiguities: list[str] = field(default_factory=list)
    requires_user_choice: bool = False
    planning_options: list[PlanningOption] = field(default_factory=list)


def _parse_count_token(token: str) -> int | None:
    normalized = token.strip().lower()
    if normalized.isdigit():
        return int(normalized)
    return _NUMBER_WORDS.get(normalized)


def _resolve_month_prefix(prefix: str) -> int | None:
    lowered = prefix.lower()
    for key, month in _MONTHS.items():
        if lowered.startswith(key):
            return month
    return None


def _parse_end_date(text: str, *, reference_year: int) -> date | None:
    match = _END_DATE_RE.search(text)
    if not match:
        return None
    day_num, month_num, year_num, day_word, month_word, year_word = match.groups()
    if day_num and month_num:
        day = int(day_num)
        month = int(month_num)
        year = int(year_num) if year_num else reference_year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None
    if day_word and month_word:
        day = int(day_word)
        month = _resolve_month_prefix(month_word)
        if month is None:
            return None
        year = int(year_word) if year_word else reference_year
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _parse_time_range(text: str) -> tuple[time, int] | None:
    match = _TIME_RANGE_RE.search(text)
    if not match:
        return None
    sh, sm, eh, em = (int(match.group(i)) for i in range(1, 5))
    start = time(sh, sm)
    end_minutes = eh * 60 + em
    start_minutes = sh * 60 + sm
    duration = end_minutes - start_minutes
    if duration <= 0:
        return None
    return start, duration


def _parse_time_from_text(text: str) -> tuple[time, int | None] | None:
    time_range = _parse_time_range(text)
    if time_range is not None:
        return time_range
    match = _TIME_AT_RE.search(text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute), None


def _weekday_from_text(text: str) -> ScheduledMeetingWeekday | None:
    match = _WEEKDAY_IN_TEXT_RE.search(text)
    if not match:
        return None
    fragment = match.group(1)
    for pattern, weekday in _WEEKDAY_FROM_TEXT:
        if pattern.search(fragment):
            return weekday
    return None


def _find_source_quote(text: str) -> str | None:
    lowered = text.lower()
    for pattern in (
        r"[^.!\n]*(?:ежеднев|кажд(?:ый|ую)\s+день|еженедел|раз\s+в\s+\d+\s+недел)[^.!\n]*",
        r"[^.!\n]*(?:на\s+\d+\s+недел|на\s+две\s+недел)[^.!\n]*",
    ):
        match = re.search(pattern, lowered, re.IGNORECASE)
        if match:
            start, end = match.span()
            quote = text[start:end].strip()
            quote = _strip_technical_tokens(quote)
            return quote if quote else None
    snippet = _strip_technical_tokens(clean_text(text))
    if snippet and len(snippet) <= 240:
        return snippet
    return snippet[:240] if snippet else None


def _strip_technical_tokens(text: str) -> str:
    tokens = [token for token in text.split() if token and not looks_like_guid(token)]
    return " ".join(tokens).strip()


def resolve_source_quote(header: dict[str, Any], combined: str) -> str | None:
    """Цитата для UI — только из текста СЗ, без GUID темы и прочих полей."""
    memo_text = extract_memo_text(header)
    if memo_text:
        return _find_source_quote(memo_text)
    return _find_source_quote(combined)


def text_implies_bounded_duration(text: str) -> bool:
    """В тексте есть срок серии, который regex-правила не разбирают — нужен LLM."""
    lowered = text.lower()
    if _ON_WEEKS_RE.search(lowered) or _ON_DAYS_RE.search(lowered):
        return False
    if _END_DATE_RE.search(lowered):
        return False
    return _BOUNDED_DURATION_RE.search(lowered) is not None


def rules_parsed_series_end(parsed: ParsedRecurrenceRules) -> bool:
    return (
        parsed.series_end_date is not None
        or parsed.end_after_weeks is not None
        or parsed.end_after_days is not None
    )


def collect_recurrence_texts(header: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for key in _TEXT_FIELD_KEYS:
        value = clean_text(header.get(key))
        if value and not looks_like_guid(value):
            texts.append(value)
    return texts


def has_recurrence_hints(texts: list[str]) -> bool:
    combined = " ".join(texts)
    if not combined.strip():
        return False
    return _RECURRENCE_HINT_RE.search(combined) is not None


def parse_recurrence_rules(text: str) -> ParsedRecurrenceRules:
    parsed = ParsedRecurrenceRules(source_quote=_find_source_quote(text))
    lowered = text.lower()

    if _WEEKDAYS_ONLY_RE.search(lowered):
        parsed.ambiguities.append(
            "Формулировка «по будням» пока не поддерживается; укажите конкретные дни или единоразовое совещание"
        )
        return parsed

    if re.search(r"ежеднев|кажд(?:ый|ую)\s+день", lowered):
        parsed.frequency = ScheduledMeetingFrequency.DAILY
        parsed.interval = 1
    elif match := _EVERY_N_DAYS_RE.search(lowered):
        parsed.frequency = ScheduledMeetingFrequency.DAILY
        parsed.interval = max(1, int(match.group(1)))
    elif match := _WEEKLY_EVERY_N_RE.search(lowered):
        parsed.frequency = ScheduledMeetingFrequency.WEEKLY
        count = _parse_count_token(match.group(1))
        parsed.interval = max(1, count or 1)
    elif re.search(r"еженедел|кажд(?:ую|ый)\s+недел", lowered):
        parsed.frequency = ScheduledMeetingFrequency.WEEKLY
        parsed.interval = 1

    if parsed.frequency is None:
        parsed.ambiguities.append("Не удалось определить периодичность серии")
        return parsed

    parsed.weekday = _weekday_from_text(lowered)

    time_parsed = _parse_time_from_text(text)
    if time_parsed is not None:
        parsed.time_local, parsed.duration_minutes = time_parsed

    if match := _ON_WEEKS_RE.search(lowered):
        count = _parse_count_token(match.group(1))
        if count is not None:
            parsed.end_after_weeks = count

    if match := _ON_DAYS_RE.search(lowered):
        count = _parse_count_token(match.group(1))
        if count is not None:
            parsed.end_after_days = count

    reference_year = date.today().year
    explicit_end = _parse_end_date(text, reference_year=reference_year)
    if explicit_end is not None:
        parsed.series_end_date = explicit_end

    return parsed


def _series_start_from_header(header: dict[str, Any]) -> date | None:
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


def _time_from_header(header: dict[str, Any]) -> tuple[time | None, int | None]:
    start_dt, end_dt = resolve_meeting_schedule(header)
    if start_dt is not None:
        time_local = start_dt.time()
        duration = schedule_duration_minutes(start_dt, end_dt)
        if duration is None:
            start_time = parse_odata_time_component(header.get("ВремяНачалаСовещания"))
            end_time = parse_odata_time_component(header.get("ВремяОкончанияСовещания"))
            if start_time and end_time:
                start_minutes = start_time[0] * 60 + start_time[1]
                end_minutes = end_time[0] * 60 + end_time[1]
                if end_minutes > start_minutes:
                    duration = end_minutes - start_minutes
        return time_local, duration
    return None, None


def _resolve_series_end(
    parsed: ParsedRecurrenceRules,
    *,
    series_start: date,
) -> date:
    if parsed.series_end_date is not None:
        return parsed.series_end_date
    if parsed.end_after_weeks is not None:
        total_days = parsed.end_after_weeks * 7
        return series_start + timedelta(days=total_days - 1)
    if parsed.end_after_days is not None:
        return series_start + timedelta(days=parsed.end_after_days - 1)
    return default_series_end_date(year=series_start.year)


def merge_with_header(
    parsed: ParsedRecurrenceRules,
    header: dict[str, Any],
    *,
    source_text: str = "",
) -> tuple[RecurrenceInput | None, list[str]]:
    ambiguities = list(parsed.ambiguities)
    if parsed.frequency is None:
        return None, ambiguities

    series_start = parsed.series_start_date or _series_start_from_header(header)
    if series_start is None:
        ambiguities.append("Не указана желаемая дата начала серии")

    header_time, header_duration = _time_from_header(header)
    if parsed.time_local is not None:
        time_local = parsed.time_local
        duration_minutes = parsed.duration_minutes
    else:
        time_local = header_time
        duration_minutes = header_duration
    if parsed.time_local is None and parsed.duration_minutes is not None:
        duration_minutes = parsed.duration_minutes

    if time_local is not None and duration_minutes is None:
        duration_minutes = DEFAULT_MEETING_DURATION_MINUTES

    if time_local is None:
        ambiguities.append("Не указано время начала серии")
    if duration_minutes is None:
        ambiguities.append("Не указана длительность серии")

    series_end = _resolve_series_end(parsed, series_start=series_start) if series_start else None
    if (
        series_start is not None
        and series_end is not None
        and series_end == default_series_end_date(year=series_start.year)
        and source_text
        and text_implies_bounded_duration(source_text)
        and not rules_parsed_series_end(parsed)
    ):
        ambiguities.append(
            "Не удалось определить срок окончания серии по правилам — нужен ответ LLM"
        )
        return None, ambiguities

    weekday = parsed.weekday
    if parsed.frequency == ScheduledMeetingFrequency.WEEKLY and weekday is None:
        if series_start is not None:
            weekday = _ISO_TO_WEEKDAY.get(series_start.weekday())
        if weekday is None:
            ambiguities.append("Не удалось определить день недели для еженедельной серии")

    if (
        series_start is None
        or series_end is None
        or time_local is None
        or duration_minutes is None
        or (parsed.frequency == ScheduledMeetingFrequency.WEEKLY and weekday is None)
    ):
        return None, ambiguities

    recurrence = RecurrenceInput(
        frequency=parsed.frequency,
        interval=max(1, parsed.interval),
        time_local=time_local,
        duration_minutes=duration_minutes,
        series_start_date=series_start,
        series_end_date=series_end,
        weekday=weekday if parsed.frequency == ScheduledMeetingFrequency.WEEKLY else None,
    )
    try:
        validate_recurrence_input(recurrence)
    except ValueError as exc:
        ambiguities.append(str(exc))
        return None, ambiguities

    return recurrence, ambiguities


def _finalize_recurrence_draft(
    recurrence: RecurrenceInput,
    ambiguities: list[str],
    *,
    source_quote: str | None,
) -> MemoRecurrenceDraft:
    confidence = _confidence_from_ambiguities(ambiguities)
    occurrence_dates = iter_occurrence_dates(recurrence)
    occurrence_count = len(occurrence_dates)
    if occurrence_count == 0:
        ambiguities.append("По правилам серии не получилось ни одного вхождения")
        return MemoRecurrenceDraft(
            is_series=True,
            confidence="low",
            recurrence=recurrence,
            source_quote=source_quote,
            ambiguities=ambiguities,
            planning_options=["single"],
        )

    recurrence_label = format_series_hint(recurrence, occurrence_count=occurrence_count)
    requires_user_choice = confidence != "low"
    planning_options: list[PlanningOption] = ["single"]
    if requires_user_choice:
        planning_options = ["series", "single"]

    return MemoRecurrenceDraft(
        is_series=True,
        confidence=confidence,
        recurrence=recurrence,
        recurrence_label=recurrence_label,
        occurrence_count=occurrence_count,
        series_start_date=recurrence.series_start_date,
        series_end_date=recurrence.series_end_date,
        series_end_label=recurrence.series_end_date.strftime("%d.%m.%Y"),
        source_quote=source_quote,
        ambiguities=ambiguities,
        requires_user_choice=requires_user_choice,
        planning_options=planning_options,
    )


def _confidence_from_ambiguities(ambiguities: list[str]) -> ConfidenceLevel:
    if not ambiguities:
        return "high"
    critical_markers = (
        "Не указана желаемая дата",
        "Не указано время",
        "Не указана длительность",
        "Не удалось определить периодичность",
        "по будням",
    )
    if any(any(marker in item for marker in critical_markers) for item in ambiguities):
        return "low"
    return "medium"


def format_series_hint(
    recurrence: RecurrenceInput,
    *,
    occurrence_count: int,
) -> str:
    base = format_recurrence_label(recurrence)
    end_label = recurrence.series_end_date.strftime("%d.%m.%Y")
    if occurrence_count == 1:
        count_part = "1 встреча"
    elif 2 <= occurrence_count <= 4:
        count_part = f"{occurrence_count} встречи"
    else:
        count_part = f"{occurrence_count} встреч"
    return f"{base} · до {end_label}, {count_part}"


def build_series_planning_read(draft: MemoRecurrenceDraft) -> dict[str, Any]:
    return {
        "detected": draft.is_series,
        "requires_user_choice": draft.requires_user_choice,
        "confidence": draft.confidence,
        "recurrence_label": draft.recurrence_label,
        "series_start_date": (
            draft.series_start_date.isoformat() if draft.series_start_date else None
        ),
        "series_end_date": draft.series_end_date.isoformat() if draft.series_end_date else None,
        "occurrence_count": draft.occurrence_count,
        "source_quote": draft.source_quote,
        "ambiguities": list(draft.ambiguities),
        "planning_options": list(draft.planning_options),
    }


def resolve_memo_recurrence_from_schedule(
    header: dict[str, Any],
) -> MemoRecurrenceDraft | None:
    from app.services.meeting_memo_schedule import parse_memo_schedule

    schedule_parsed = parse_memo_schedule(header)
    if schedule_parsed is None:
        return None
    if schedule_parsed.frequency is None and not schedule_parsed.ambiguities:
        return None

    recurrence, ambiguities = merge_with_header(schedule_parsed, header)
    if recurrence is not None:
        return _finalize_recurrence_draft(
            recurrence,
            ambiguities,
            source_quote=schedule_parsed.source_quote,
        )
    if schedule_parsed.frequency is not None or any(
        "единоразовое" not in item.lower() for item in schedule_parsed.ambiguities
    ):
        return MemoRecurrenceDraft(
            is_series=True,
            confidence=_confidence_from_ambiguities(schedule_parsed.ambiguities),
            source_quote=schedule_parsed.source_quote,
            ambiguities=schedule_parsed.ambiguities,
            planning_options=["single"],
        )
    return None


_resolve_from_schedule = resolve_memo_recurrence_from_schedule


def resolve_memo_recurrence_rules(
    header: dict[str, Any],
    document: dict[str, Any] | None = None,
) -> MemoRecurrenceDraft:
    del document
    combined = " ".join(collect_recurrence_texts(header))
    parsed = parse_recurrence_rules(combined)
    parsed.source_quote = resolve_source_quote(header, combined)
    recurrence, ambiguities = merge_with_header(parsed, header, source_text=combined)
    if recurrence is None:
        return MemoRecurrenceDraft(
            is_series=True,
            confidence="low",
            source_quote=parsed.source_quote,
            ambiguities=ambiguities,
            planning_options=["single"],
        )
    return _finalize_recurrence_draft(
        recurrence,
        ambiguities,
        source_quote=parsed.source_quote,
    )


def resolve_memo_recurrence(
    header: dict[str, Any],
    document: dict[str, Any] | None = None,
) -> MemoRecurrenceDraft:
    """Синхронный резолвер: расписание 1С и заглушка для текстовых намёков (LLM — в async)."""
    del document

    schedule_draft = resolve_memo_recurrence_from_schedule(header)
    if schedule_draft is not None:
        return schedule_draft

    texts = collect_recurrence_texts(header)
    if not has_recurrence_hints(texts):
        return MemoRecurrenceDraft(
            is_series=False,
            confidence="high",
            planning_options=["single"],
        )

    combined = " ".join(texts)
    return MemoRecurrenceDraft(
        is_series=True,
        confidence="medium",
        source_quote=resolve_source_quote(header, combined),
        ambiguities=["Планирование серии уточняется по тексту СЗ"],
        planning_options=["single"],
        requires_user_choice=False,
    )
