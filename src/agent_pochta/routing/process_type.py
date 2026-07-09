"""Определение вида процесса документа (рассмотрение / исполнение / ознакомление)."""

from __future__ import annotations

import re

PROCESS_RASSMOTRENIYE = "рассмотрение"
PROCESS_ISPOLNENIYE = "исполнение"
PROCESS_OZNAKOMLENIYE = "ознакомление"

VALID_PROCESS_TYPES = frozenset(
    {PROCESS_RASSMOTRENIYE, PROCESS_ISPOLNENIYE, PROCESS_OZNAKOMLENIYE}
)

_PROCESS_ALIASES = {
    "review": PROCESS_RASSMOTRENIYE,
    "consideration": PROCESS_RASSMOTRENIYE,
    "execution": PROCESS_ISPOLNENIYE,
    "fulfillment": PROCESS_ISPOLNENIYE,
    "familiarization": PROCESS_OZNAKOMLENIYE,
    "acknowledgement": PROCESS_OZNAKOMLENIYE,
    "acknowledgment": PROCESS_OZNAKOMLENIYE,
    "information": PROCESS_OZNAKOMLENIYE,
    "info": PROCESS_OZNAKOMLENIYE,
}

_OZNAKOMLENIYE_MARKERS = (
    "уведомлен",
    "информация о срок",
    "информирование",
    "для сведения",
    "к сведению",
    "информируем",
    "сообщаем вам",
    "сообщаем, что",
    "статус отгруз",
    "статус постав",
    "сроки отгруз",
    "срок отгруз",
    "оповещен",
    "напоминаем",
    "fyi",
    "for your information",
)

_RASSMOTRENIYE_MARKERS = (
    "претенз",
    "согласова",
    "на согласован",
    "требует решения",
    "требует рассмотр",
    "просьба рассмотр",
    "рассмотреть",
    "рассмотрение",
    "утвердить",
    "утвержден",
)

_ISPOLNENIYE_MARKERS = (
    "запрос на",
    "просим выстав",
    "просим направ",
    "необходимо выполн",
    "необходимо предостав",
    "выставить сч",
    "выставьте сч",
    "направить акт",
    "направьте акт",
    "подготовить",
    "выполнить заказ",
    "выполнить работ",
    "предоставить документ",
    "просим предостав",
)

# «счёт/счет» как действие, но не «по счету №» (ссылка на документ).
_ACTION_INVOICE_RE = re.compile(
    r"(?:выстав(?:ить|ьте|лен)|сч[её]т\s+на\s+оплат|запрос\s+.*сч[её]т)",
    re.IGNORECASE,
)


def normalize_process_type(raw: str | None) -> str | None:
    """Нормализует process_type от LLM к одному из трёх допустимых значений."""
    value = (raw or "").strip().lower()
    if not value:
        return None
    if value in VALID_PROCESS_TYPES:
        return value
    return _PROCESS_ALIASES.get(value)


def infer_process_type_heuristic(
    subject: str = "",
    combined_text: str = "",
    *,
    claim: bool = False,
) -> str:
    """Эвристика по ключевым словам, если LLM не вернул process_type."""
    combined = f"{subject} {combined_text}".lower()

    if any(marker in combined for marker in _OZNAKOMLENIYE_MARKERS):
        return PROCESS_OZNAKOMLENIYE

    if claim or any(marker in combined for marker in _RASSMOTRENIYE_MARKERS):
        return PROCESS_RASSMOTRENIYE

    if any(marker in combined for marker in _ISPOLNENIYE_MARKERS):
        return PROCESS_ISPOLNENIYE

    if _ACTION_INVOICE_RE.search(combined):
        return PROCESS_ISPOLNENIYE

    if "акт свер" in combined or re.search(r"\bакт\b", combined):
        return PROCESS_ISPOLNENIYE

    if re.search(r"\bзапрос\b", combined):
        return PROCESS_ISPOLNENIYE

    if claim:
        return PROCESS_RASSMOTRENIYE
    return PROCESS_ISPOLNENIYE


def resolve_process_type(
    *,
    llm_process: str | None = None,
    subject: str = "",
    combined_text: str = "",
    claim: bool = False,
) -> str:
    """LLM — основной источник; эвристики — запасной; default — исполнение (или рассмотрение для претензий)."""
    normalized = normalize_process_type(llm_process)
    if normalized:
        return normalized
    return infer_process_type_heuristic(subject, combined_text, claim=claim)
