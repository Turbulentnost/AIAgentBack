"""Конструктор ссылок на источники (``source_reference``, п. 4.7 ТЗ-ПЛАТФ-001).

Каждый существенный вывод агента обязан содержать: документ, раздел/пункт, версию,
дату обращения и статус надёжности источника. Проверка T3, KPI «доля выводов со ссылкой».
"""

from __future__ import annotations

from typing import Any, Literal

SourceStatus = Literal[
    "официальный",
    "проверенный",
    "неофициальный",
    "требует проверки",
    "устаревший",
    "заменён",
    "отменён",
]


def source_ref(
    document: str,
    *,
    section: str = "",
    clause: str = "",
    version: str = "",
    accessed_at: str = "",
    status: SourceStatus = "проверенный",
    url: str = "",
) -> dict[str, Any]:
    """Формирует нормализованную ссылку на источник.

    ``accessed_at`` — дата обращения (ISO ``YYYY-MM-DD``); передаётся вызывающим узлом,
    время внутри SDK не берётся, чтобы обеспечить детерминизм тестов.
    """

    return {
        "document": document,
        "section": section,
        "clause": clause,
        "version": version,
        "accessed_at": accessed_at,
        "status": status,
        "url": url,
    }
