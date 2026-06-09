from __future__ import annotations

# Builder/meta tools that belong to the agent-constructor itself and must never
# leak into a designed agent's blueprint, preview grounding, or sandbox execution.
BUILDER_META_TOOLS: frozenset[str] = frozenset(
    {
        "list_available_tools",
        "get_tool_description",
        "search_agent_templates",
        "save_agent_blueprint",
        "validate_agent_blueprint",
        "render_workflow_graph",
    }
)
