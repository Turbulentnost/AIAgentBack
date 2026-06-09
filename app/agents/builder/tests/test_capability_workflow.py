from __future__ import annotations

from app.agents.builder.capabilities import (
    collect_runtime_tool_hints,
    render_capability_workflow_graph,
)
from app.agents.builder.templates.consultant import CONSULTANT_WORKFLOW_TEMPLATE
from app.agents.builder.tools import build_default_blueprint
from app.agents.builder.validators import validate_agent_blueprint
from app.models.enums import AgentType


def test_render_capability_workflow_graph_contains_capabilities():
    graph = render_capability_workflow_graph(CONSULTANT_WORKFLOW_TEMPLATE)
    step_nodes = [node for node in graph["nodes"] if node.get("type") == "step"]
    capabilities = [node.get("capability") for node in step_nodes]

    assert capabilities == [
        "receive_question",
        "knowledge_search",
        "rag_retrieval",
        "llm_answer",
        "present_answer",
    ]
    assert all(capability != "search_knowledge_base" for capability in capabilities if capability)
    assert graph["edges"]


def test_collect_runtime_tool_hints_from_capabilities():
    tools = collect_runtime_tool_hints(CONSULTANT_WORKFLOW_TEMPLATE)
    assert "search_knowledge_base" in tools
    assert "search_knowledge_base" not in [
        step.get("capability") for step in CONSULTANT_WORKFLOW_TEMPLATE
    ]


def test_build_default_blueprint_consultant_capability_graph():
    blueprint = build_default_blueprint(
        goal="Консультант по внутренним регламентам",
        requirements={
            "agent_type": AgentType.CONSULTANT.value,
            "workflow_capability_steps": CONSULTANT_WORKFLOW_TEMPLATE,
            "knowledge_bases": ["hr-policies"],
        },
        tools=["search_knowledge_base"],
    )

    validation = validate_agent_blueprint(blueprint)
    assert validation["valid"] is True
    assert blueprint["agent_type"] == AgentType.CONSULTANT.value

    step_nodes = [
        node
        for node in blueprint["workflow_graph"]["nodes"]
        if node.get("type") == "step"
    ]
    assert all(node.get("capability") or node.get("goal") for node in step_nodes)
    assert all(node.get("id") != "search_knowledge_base" for node in step_nodes)
