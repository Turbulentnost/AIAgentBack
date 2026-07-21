"""Checkpointer LangGraph для ролевых агентов ОМТО (сохранение состояния HITL).

Точки HITL реализованы через ``interrupt`` LangGraph: при приостановке состояние
графа сохраняется в checkpointer под ключом ``thread_id`` (= correlation_id кейса),
а при подтверждении человеком граф возобновляется с того же места.

По умолчанию используется процессный ``InMemorySaver`` — он обеспечивает
паузу/возобновление в пределах одного процесса приложения. Для устойчивого
возобновления между воркерами/перезапусками включите Postgres-checkpointer
(``settings`` флаг / переменная окружения ``OMTO_LANGGRAPH_CHECKPOINTER=postgres``):
LangGraph-saver будет писать в ту же БД. Переключение НЕ требует изменений в графах,
раннере или дашборде — меняется только фабрика ниже.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

try:  # LangGraph ≥ 0.2: InMemorySaver (ранее MemorySaver)
    from langgraph.checkpoint.memory import InMemorySaver as _MemorySaver
except Exception:  # pragma: no cover
    from langgraph.checkpoint.memory import MemorySaver as _MemorySaver  # type: ignore


@lru_cache(maxsize=1)
def _memory_saver() -> Any:
    return _MemorySaver()


def _postgres_saver() -> Any | None:
    """Опциональный durable-checkpointer в Postgres (если пакет и БД доступны)."""
    dsn = os.getenv("OMTO_LANGGRAPH_PG_DSN")
    if not dsn:
        return None
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except Exception:  # pragma: no cover — пакет не установлен
        return None
    saver = PostgresSaver.from_conn_string(dsn)
    saver.setup()  # идемпотентно создаёт таблицы чекпоинтов
    return saver


def get_checkpointer() -> Any:
    """Возвращает checkpointer графа: Postgres (если настроен) иначе in-memory."""
    if os.getenv("OMTO_LANGGRAPH_CHECKPOINTER", "").lower() == "postgres":
        pg = _postgres_saver()
        if pg is not None:
            return pg
    return _memory_saver()


__all__ = ["get_checkpointer"]
