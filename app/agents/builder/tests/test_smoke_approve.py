from __future__ import annotations

from app.agents.builder.tools import build_default_blueprint
from app.agents.builder.validators import validate_agent_blueprint


def test_smoke_blueprint_approve_flow():
    """Smoke: сформировать blueprint, провалидировать и убедиться что approve допустим."""
    blueprint = build_default_blueprint(
        goal="Автоматизировать проверку изменений НД",
        requirements={
            "inputs": "Текст изменения и реквизиты приказа",
            "outputs": "Проект новой редакции и diff",
            "human_approval": True,
            "recommended_tools": ["search_knowledge_base", "get_document_text"],
        },
        tools=["search_knowledge_base", "get_document_text"],
    )
    validation = validate_agent_blueprint(blueprint)
    assert validation["valid"] is True
    assert blueprint["agent_card"]["name"]
    assert blueprint["workflow_graph"]["nodes"]
