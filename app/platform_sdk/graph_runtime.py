"""Совместимый с LangGraph минимальный runtime для скелета агентов.

ТЗ мандатирует LangGraph как ядро агентной логики (п. 6.2). В настоящем каркасе граф
собирается через API-подмножество LangGraph (``StateGraph``/``START``/``END``/
``add_node``/``add_edge``/``add_conditional_edges``/``compile``/``invoke``), а исполняется
встроенным мини-runtime — чтобы граф проходил end-to-end без установленной ``langgraph``.

Переключение на боевой LangGraph — заменой импорта в ``graph.py`` агента на::

    from langgraph.graph import StateGraph, START, END

Топология (узлы, рёбра, ветвления) при этом не меняется. Точки HITL в проде
реализуются через ``interrupt`` LangGraph + checkpointer (PostgreSQL); здесь узел HITL
исполняется как обычный узел, помечающий состояние (реальной приостановки нет).
"""

from __future__ import annotations

from typing import Any, Callable

START = "__start__"
END = "__end__"

NodeFn = Callable[[dict[str, Any]], dict[str, Any] | None]
Router = Callable[[dict[str, Any]], str]


class _Compiled:
    """Скомпилированный граф с методом ``invoke`` (последовательный обход)."""

    def __init__(self, graph: "StateGraph") -> None:
        self._g = graph

    def invoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
        *,
        max_steps: int = 1000,
    ) -> dict[str, Any]:
        g = self._g
        state = dict(state)
        current = g._entry
        steps = 0

        while current != END and steps < max_steps:
            steps += 1
            fn = g._nodes.get(current)
            if fn is None:
                raise KeyError(f"Узел не найден: {current}")

            update = fn(state)
            if update:
                state = _merge(state, update)

            # трасса перехода (correlation_id — обязательно, п. 6.3)
            state.setdefault("audit", []).append(
                {"node": current, "correlation_id": state.get("correlation_id", "")}
            )

            current = _next(g, current, state)

        return state


def _merge(state: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Слияние обновления узла в состояние (списки-накопители расширяются)."""

    out = dict(state)
    for key, value in update.items():
        if isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = out[key] + value
        else:
            out[key] = value
    return out


def _next(graph: "StateGraph", current: str, state: dict[str, Any]) -> str:
    if current in graph._cond:
        router, mapping = graph._cond[current]
        key = router(state)
        if key not in mapping:
            raise KeyError(
                f"Ветвление из '{current}': роутер вернул '{key}', "
                f"нет в отображении {list(mapping)}"
            )
        return mapping[key]
    return graph._edges.get(current, END)


class StateGraph:
    """Минимальная реализация построителя графа, совместимая с LangGraph по API."""

    def __init__(self, state_schema: Any = None) -> None:
        self.state_schema = state_schema
        self._nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, str] = {}
        self._cond: dict[str, tuple[Router, dict[str, str]]] = {}
        self._entry: str = START

    def add_node(self, name: str, fn: NodeFn) -> "StateGraph":
        if name in (START, END):
            raise ValueError("Имена START/END зарезервированы")
        self._nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str) -> "StateGraph":
        if src == START:
            self._entry = dst
        else:
            self._edges[src] = dst
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        self._entry = name
        return self

    def add_conditional_edges(
        self, src: str, router: Router, mapping: dict[str, str]
    ) -> "StateGraph":
        self._cond[src] = (router, mapping)
        return self

    def compile(self, checkpointer: Any = None) -> _Compiled:
        # checkpointer в мини-runtime игнорируется; в проде — PostgreSQL saver.
        if self._entry == START:
            raise ValueError("Не задана точка входа графа (add_edge(START, ...))")
        return _Compiled(self)
