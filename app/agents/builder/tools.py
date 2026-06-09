from __future__ import annotations

import re
import uuid
from typing import Any

from typing import TYPE_CHECKING

from app.agents.builder.capabilities import (
    collect_runtime_tool_hints,
    render_capability_workflow_graph,
)
from app.agents.builder.prompts import DEFAULT_PLAN_STEPS
from app.agents.builder.templates.consultant import CONSULTANT_WORKFLOW_TEMPLATE
from app.models.enums import AgentType

if TYPE_CHECKING:
    from app.agents.builder.llm import BlueprintLLMResponse


def slugify_code(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", name.lower()).strip("_")
    return value[:120] or f"agent_{uuid.uuid4().hex[:8]}"


def list_available_tools_catalog() -> list[dict[str, Any]]:
    from app.agents.tools.registry import agent_tool_registry

    return [
        {
            "name": tool.name,
            "description": tool.description,
            "implemented": tool.implemented,
            "required_permissions": tool.required_permissions,
        }
        for tool in agent_tool_registry.list()
    ]


def render_workflow_graph(steps: list[str]) -> dict[str, Any]:
    nodes = [{"id": "start", "label": "Старт", "type": "start"}]
    edges: list[dict[str, str]] = []
    prev = "start"
    for index, step in enumerate(steps, start=1):
        node_id = f"step_{index}"
        nodes.append({"id": node_id, "label": step, "type": "step"})
        edges.append({"source": prev, "target": node_id})
        prev = node_id
    nodes.append({"id": "end", "label": "Завершение", "type": "end"})
    edges.append({"source": prev, "target": "end"})
    return {"nodes": nodes, "edges": edges}


def build_default_blueprint(goal: str, requirements: dict[str, Any], tools: list[str]) -> dict[str, Any]:
    name = requirements.get("agent_name") or goal[:80]
    agent_type = requirements.get("agent_type")
    capability_steps = requirements.get("workflow_capability_steps")
    if agent_type == AgentType.CONSULTANT.value and capability_steps:
        workflow_graph = render_capability_workflow_graph(
            capability_steps,
            human_approval=bool(requirements.get("human_approval")),
        )
    else:
        workflow_steps = requirements.get("workflow_steps") or [
            "Получение входных данных",
            "Обработка",
            "Формирование результата",
        ]
        if requirements.get("human_approval"):
            workflow_steps.append("Согласование с пользователем")
        workflow_graph = render_workflow_graph(workflow_steps)
    return {
        "agent_type": agent_type,
        "agent_card": {
            "name": name,
            "purpose": goal,
            "roles": requirements.get("roles", []),
        },
        "input_schema": requirements.get("input_schema", {"type": "object", "properties": {}}),
        "output_schema": requirements.get("output_schema", {"type": "object", "properties": {}}),
        "tools": tools,
        "knowledge_bases": requirements.get("knowledge_bases", []),
        "workflow_graph": workflow_graph,
        "human_approval_rules": requirements.get("human_approval_rules", []),
        "prompts": {
            "system": requirements.get("system_prompt", f"Ты агент для задачи: {goal}"),
            "developer": requirements.get("developer_prompt", ""),
        },
        "task_statuses": requirements.get("task_statuses", ["draft", "running", "completed", "failed"]),
        "finding_schema": requirements.get("finding_schema", {}),
        "report_template": requirements.get("report_template", {}),
        "constraints": requirements.get("constraints", []),
        "test_cases": requirements.get("test_cases", []),
    }


def default_plan_steps() -> list[dict[str, str]]:
    return [{"title": title, "description": description} for title, description in DEFAULT_PLAN_STEPS]


def blueprint_from_llm(goal: str, llm: "BlueprintLLMResponse", requirements: dict[str, Any]) -> dict[str, Any]:
    from app.agents.tools.registry import agent_tool_registry

    allowed_tools = {tool.name for tool in agent_tool_registry.list() if tool.implemented}
    tools = [tool for tool in llm.tools if tool in allowed_tools]
    if not tools:
        tools = list(requirements.get("recommended_tools") or [])[:5]
    if not tools:
        tools = [tool.name for tool in agent_tool_registry.list() if tool.implemented][:5]

    agent_type = requirements.get("agent_type")
    workflow_capability_steps: list[dict[str, str]] = []
    if llm.workflow_nodes:
        workflow_capability_steps = [node.model_dump() for node in llm.workflow_nodes]
    elif agent_type == AgentType.CONSULTANT.value:
        workflow_capability_steps = [dict(step) for step in CONSULTANT_WORKFLOW_TEMPLATE]

    workflow_steps = llm.workflow_steps or [
        "Получение входных данных",
        "Обработка",
        "Формирование результата",
    ]
    if llm.human_approval:
        workflow_steps = [*workflow_steps, "Согласование с пользователем"]

    if not tools and workflow_capability_steps:
        tools = collect_runtime_tool_hints(workflow_capability_steps)

    merged_requirements = {
        **requirements,
        "agent_type": agent_type,
        "agent_name": llm.agent_name,
        "workflow_capability_steps": workflow_capability_steps,
        "input_schema": llm.input_schema,
        "output_schema": llm.output_schema,
        "knowledge_bases": llm.knowledge_bases,
        "human_approval": llm.human_approval,
        "human_approval_rules": llm.human_approval_rules,
        "system_prompt": llm.system_prompt,
        "developer_prompt": llm.developer_prompt,
        "test_cases": llm.test_cases,
        "constraints": llm.constraints,
        "workflow_steps": workflow_steps,
    }
    blueprint = build_default_blueprint(goal, merged_requirements, tools)
    blueprint["agent_card"]["name"] = llm.agent_name
    blueprint["agent_card"]["purpose"] = llm.purpose or goal
    return blueprint
