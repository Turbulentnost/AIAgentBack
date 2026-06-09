from __future__ import annotations

from app.agents.builder.templates.consultant import CONSULTANT_WORKFLOW_TEMPLATE
from app.agents.builder.tools import build_default_blueprint
from app.agents.builder.validators import validate_agent_blueprint
from app.models.enums import AgentType


def test_smoke_blueprint_approve_flow_consultant():
    """Smoke: consultant blueprint с capability-графом проходит валидацию."""
    blueprint = build_default_blueprint(
        goal="Консультант по внутренним регламентам компании",
        requirements={
            "agent_type": AgentType.CONSULTANT.value,
            "workflow_capability_steps": CONSULTANT_WORKFLOW_TEMPLATE,
            "knowledge_bases": ["company-policies"],
            "knowledge_sources": "search_knowledge_base: Поиск в БЗ",
            "knowledge_sources_auto": True,
            "required_elements": [
                {
                    "key": "knowledge_sources",
                    "value": "search_knowledge_base: Поиск в БЗ",
                    "auto_resolved": True,
                    "required": False,
                    "status": "filled",
                },
                {
                    "key": "search_approach",
                    "value": "RAG",
                    "required": True,
                    "status": "filled",
                    "confidence": 0.9,
                },
            ],
        },
        tools=["search_knowledge_base"],
    )
    validation = validate_agent_blueprint(blueprint)
    assert validation["valid"] is True
    assert blueprint["agent_type"] == AgentType.CONSULTANT.value
    assert blueprint["agent_card"]["name"]

    step_nodes = [
        node
        for node in blueprint["workflow_graph"]["nodes"]
        if node.get("type") == "step"
    ]
    assert step_nodes
    assert all(node.get("capability") for node in step_nodes)
