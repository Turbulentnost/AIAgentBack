"""Тест singleton runtime worker."""

from __future__ import annotations

from agent_pochta.workers import runtime


def test_worker_container_is_singleton():
    runtime.reset_worker_runtime()
    first = runtime.get_worker_container()
    second = runtime.get_worker_container()
    assert first is second
    runtime.reset_worker_runtime()


def test_worker_graph_is_singleton():
    runtime.reset_worker_runtime()
    first = runtime.get_worker_graph()
    second = runtime.get_worker_graph()
    assert first is second
    runtime.reset_worker_runtime()
