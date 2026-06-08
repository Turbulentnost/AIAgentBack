from __future__ import annotations

import pytest

from app.agents.builder.graph import (
    build_graph,
    route_after_clarify,
    route_after_create_plan,
    route_after_execute,
)


def test_graph_compiles():
    graph = build_graph()
    assert graph is not None


def test_route_after_clarify_waits_for_user():
    assert route_after_clarify({"requires_user_input": True}) != "create_plan"


def test_route_after_create_plan_fails_on_llm_error():
    from langgraph.graph import END

    from app.models.enums import AgentBuilderSessionStatus

    assert route_after_create_plan({"status": AgentBuilderSessionStatus.FAILED.value}) == END


def test_route_after_execute_loops_steps():
    steps = [{"title": "A"}, {"title": "B"}]
    assert route_after_execute({"current_step_index": 0, "plan_steps": steps}) == "execute_plan_step"
    assert route_after_execute({"current_step_index": 2, "plan_steps": steps}) == "collect_requirements"
