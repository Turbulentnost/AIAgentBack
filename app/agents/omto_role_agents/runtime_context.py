"""Мост sync-граф → async-сервисы платформы для ролевых агентов ОМТО.

Скопированные графы агентов синхронны (узлы — обычные ``def``), а боевые сервисы
платформы (LLM-шлюз, RAG-ретривер, 1С MCP) — ``async``. Переписывать десятки узлов
в ``async`` дорого и рискованно, поэтому применяется мост:

* эндпоинт (async) собирает :class:`AgentRuntime` с ссылкой на текущий event loop и
  боевыми сервисами и запускает синхронный граф в отдельном потоке
  (``asyncio.to_thread`` — копирует contextvars);
* синхронные функции SDK (``llm_complete``/``hybrid_search``/``call_tool``) достают
  runtime из :data:`_CURRENT` и выполняют корутину сервиса на главном loop через
  :func:`run_async` (``run_coroutine_threadsafe`` + блокирующий ``.result()`` в потоке).

Если runtime не привязан (dry-run, юнит-тесты, вызов без обёртки) — функции SDK
возвращают прежние детерминированные мок-данные. Это сохраняет обратную совместимость
и позволяет графам исполняться без боевых сервисов.
"""

from __future__ import annotations

import asyncio
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Awaitable, Iterator

_CURRENT: contextvars.ContextVar["AgentRuntime | None"] = contextvars.ContextVar(
    "omto_agent_runtime", default=None
)


@dataclass
class AgentRuntime:
    """Боевые сервисы и event loop для одного исполнения графа."""

    loop: asyncio.AbstractEventLoop
    llm: Any | None = None          # app.llm.gateway.llm_gateway (async .chat)
    retriever: Any | None = None    # app.knowledge_base.retriever.retriever (async .retrieve)
    mcp: Any | None = None          # OneCMCPClient (async .call_capability)
    default_model: str | None = None
    bridge_timeout: float = 660.0
    correlation_id: str = ""


def current_runtime() -> "AgentRuntime | None":
    return _CURRENT.get()


@contextmanager
def bind_runtime(runtime: "AgentRuntime") -> Iterator[None]:
    token = _CURRENT.set(runtime)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def run_async(coro: Awaitable[Any], *, timeout: float | None = None) -> Any:
    """Выполняет корутину боевого сервиса на главном loop из потока графа.

    Вызывается только из синхронного кода узла, исполняемого в отдельном потоке.
    Блокирует поток графа (не главный loop) до результата.
    """
    runtime = current_runtime()
    if runtime is None:
        raise RuntimeError("AgentRuntime не привязан: мост sync→async недоступен")
    future = asyncio.run_coroutine_threadsafe(coro, runtime.loop)
    return future.result(timeout or runtime.bridge_timeout)


def build_runtime(*, correlation_id: str = "") -> AgentRuntime:
    """Собирает runtime с боевыми сервисами платформы (глобальные синглтоны).

    Вызывать в async-контексте (нужен работающий event loop). Если сервис/конфиг
    недоступен (нет env, импорт упал) — соответствующее поле остаётся ``None``, и
    SDK-функция откатывается на мок. Никогда не бросает — деградирует мягко.
    """
    loop = asyncio.get_running_loop()
    runtime = AgentRuntime(loop=loop, correlation_id=correlation_id)

    try:
        from app.llm.gateway import llm_gateway

        runtime.llm = llm_gateway
    except Exception:  # noqa: BLE001 — нет конфигурации LLM: останется мок
        runtime.llm = None
    try:
        from app.core.config import settings

        runtime.default_model = getattr(settings, "LLM_DEFAULT_MODEL", None)
    except Exception:  # noqa: BLE001
        runtime.default_model = None
    try:
        from app.knowledge_base.retriever import retriever

        runtime.retriever = retriever
    except Exception:  # noqa: BLE001 — нет Qdrant/эмбеддера: останется мок
        runtime.retriever = None
    try:
        from app.agents.procurement_agent.mcp_client import OneCMCPClient

        runtime.mcp = OneCMCPClient(timeout_seconds=650, max_attempts=2)
    except Exception:  # noqa: BLE001 — нет MCP: останется мок/unavailable
        runtime.mcp = None
    return runtime


__all__ = [
    "AgentRuntime",
    "bind_runtime",
    "build_runtime",
    "current_runtime",
    "run_async",
]
