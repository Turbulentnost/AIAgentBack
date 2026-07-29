"""Фильтрация мета-комментариев LLM из пользовательских отчётов."""

from __future__ import annotations

import re
from typing import Any

_LLM_META_PHRASES: tuple[str, ...] = (
    "на основе предоставленного индекса",
    "отсутствуют позиции",
    "не могут быть выявлены",
    "отсутствия детализированных данных",
    "детализированных данных в индексе",
    "проверка комплекта кд выполнена",
    "для анализа перекрёстных ссылок",
    "для анализа перекrestных ссылок",
    "в данных отсутствуют",
    "positions_in_spec",
    "positions_on_drawing",
    "document_index",
    "pipeline:",
    "extract → rules",
    "extract -> rules",
    "агрегированного json",
    "rules_only",
    "stage 2b",
    "stage 2",
    "без изображений",
    "содержимое листов (sheets)",
)

_LLM_STATUS_ONLY_PHRASES: tuple[str, ...] = (
    "не выявлено ошибок",
    "нарушений не выявлено",
    "нарушений не обнаружено",
    "замечаний нет",
    "ошибок не найдено",
    "нарушений не найдено",
)

_META_STATUS_RE = re.compile(
    r"^\s*(?:проверка комплекта|анализ выполнен|оценка выполнена|итог проверки)",
    re.IGNORECASE,
)


def is_llm_meta_text(text: str) -> bool:
    """True, если текст — служебный комментарий LLM, а не конкретное нарушение ГОСТ."""
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return False

    lower = normalized.lower()
    if any(phrase in lower for phrase in _LLM_META_PHRASES):
        return True
    if _META_STATUS_RE.match(normalized):
        return True
    if re.search(r"нарушени[яий].*не могут быть", lower):
        return True
    if re.search(r"не выявлен[ыо].*из-за", lower):
        return True
    if len(normalized) <= 120 and any(phrase in lower for phrase in _LLM_STATUS_ONLY_PHRASES):
        if "гост" not in lower and not re.search(r"позици[яи]\s+\d", lower):
            return True
    return False


def filter_llm_report_text(text: str) -> str:
    """Оставляет только текст с конкретными нарушениями; пусто, если только мета."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if is_llm_meta_text(raw):
        return ""

    parts = re.split(r"\n\s*\n", raw)
    kept: list[str] = []
    for part in parts:
        chunk = part.strip()
        if not chunk or is_llm_meta_text(chunk):
            continue
        kept.append(chunk)
    return "\n\n".join(kept).strip()
