"""Нормализация email-адресов (ТЗ §8.2 шаг 1)."""

from __future__ import annotations

import re


def normalize_email_address(address: str, aliases: dict[str, str] | None = None) -> str:
    addr = address.lower().strip()
    if aliases and addr in aliases:
        return aliases[addr]
    if "@" not in addr and aliases:
        candidate = f"{addr}@turbo-don.ru"
        if candidate in aliases.values():
            return candidate
    return addr


def normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def keyword_in_text(keyword: str, text: str) -> bool:
    """Проверяет ключевое слово в тексте; короткие токены — только по границам слова."""
    kw = keyword.lower().strip()
    if not kw:
        return False
    if len(kw) <= 3:
        return bool(re.search(rf"(?<![а-яёa-z0-9]){re.escape(kw)}(?![а-яёa-z0-9])", text))
    return kw in text


def contains_claim_marker(text: str) -> bool:
    """Определяет claim=true по юридическим маркерам без ложных «иск» в «риск/исключение»."""
    normalized = normalize_text(text)
    if not normalized:
        return False

    if "арбитраж" in normalized or "исполнительный лист" in normalized or "судебн" in normalized:
        return True

    if re.search(r"(?<![а-яёa-z0-9])иск(?![а-яёa-z0-9])", normalized):
        return True

    if re.search(r"(?<![а-яёa-z0-9])суд(?![а-яёa-z0-9])", normalized):
        return True

    if "претенз" in normalized and not re.search(r"без(?:\s+\w+){0,4}\s+претенз", normalized):
        return True

    if "требован" in normalized and not re.search(r"без(?:\s+\w+){0,4}\s+требован", normalized):
        return True

    return False
