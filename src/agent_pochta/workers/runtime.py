"""Singleton runtime для Celery worker — переиспользование клиентов между задачами."""

from __future__ import annotations

from typing import Any

from agent_pochta.services import ServiceContainer, build_container

_container: ServiceContainer | None = None
_graph: Any | None = None


def get_worker_container() -> ServiceContainer:
    """Один контейнер сервисов на процесс worker (Qdrant, LLM, …)."""
    global _container
    if _container is None:
        _container = build_container()
    return _container


def get_worker_graph():
    """Скомпилированный LangGraph — один раз на процесс worker."""
    global _graph
    if _graph is None:
        from agent_pochta.graph import build_graph

        _graph = build_graph(get_worker_container())
    return _graph


def reset_worker_runtime() -> None:
    """Закрывает клиенты и сбрасывает singleton (тесты / перезагрузка конфига)."""
    global _container, _graph
    if _container is not None:
        llm = _container.llm
        if hasattr(llm, "close"):
            llm.close()
        rag = _container.rag
        if hasattr(rag, "close"):
            rag.close()
    _container = None
    _graph = None
