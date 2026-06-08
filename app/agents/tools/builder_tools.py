from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.builder.tools import (
    build_default_blueprint,
    list_available_tools_catalog,
    render_workflow_graph,
    slugify_code,
)
from app.agents.builder.validators import validate_agent_blueprint
from app.agents.tools.registry import AgentToolDefinition, register_tool
from app.agents.tools.schemas import EmptyToolInput, ToolContext
from app.models.agent_blueprint import AgentBlueprint
from app.models.enums import AgentBlueprintStatus


class GetToolDescriptionInput(BaseModel):
    tool_name: str


class SearchAgentTemplatesInput(BaseModel):
    query: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


class SaveBuilderPlanInput(BaseModel):
    session_id: str
    goal: str
    steps: list[dict[str, str]]


class SaveBuilderStepResultInput(BaseModel):
    session_id: str
    step_order: int
    result: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class SaveBuilderAttemptInput(BaseModel):
    session_id: str
    goal: str | None = None
    success: bool = False
    result_summary: str | None = None
    failure_reason: str | None = None
    input_context: dict[str, Any] = Field(default_factory=dict)


class SaveAgentBlueprintInput(BaseModel):
    session_id: str
    blueprint: dict[str, Any]


class ValidateAgentBlueprintInput(BaseModel):
    blueprint: dict[str, Any]


class RenderWorkflowGraphInput(BaseModel):
    steps: list[str]


async def list_available_tools(_: EmptyToolInput, __: ToolContext) -> dict[str, Any]:
    return {"items": list_available_tools_catalog()}


async def get_tool_description(payload: GetToolDescriptionInput, _: ToolContext) -> dict[str, Any]:
    for item in list_available_tools_catalog():
        if item["name"] == payload.tool_name:
            return item
    return {"name": payload.tool_name, "description": "Инструмент не найден", "implemented": False}


async def search_agent_templates(payload: SearchAgentTemplatesInput, context: ToolContext) -> dict[str, Any]:
    stmt = select(AgentBlueprint).where(AgentBlueprint.status == AgentBlueprintStatus.APPROVED)
    if payload.query:
        pattern = f"%{payload.query}%"
        stmt = stmt.where(AgentBlueprint.name.ilike(pattern) | AgentBlueprint.description.ilike(pattern))
    stmt = stmt.limit(payload.limit)
    result = await context.db.execute(stmt)
    items = [
        {
            "id": str(item.id),
            "name": item.name,
            "code": item.code,
            "description": item.description,
            "status": item.status.value,
        }
        for item in result.scalars().all()
    ]
    return {"items": items}


async def save_agent_blueprint(payload: SaveAgentBlueprintInput, context: ToolContext) -> dict[str, Any]:
    from app.services.agent_builder_service import AgentBuilderService

    blueprint = await AgentBuilderService(context.db).save_blueprint_draft(
        payload.session_id,
        payload.blueprint,
        current_user=context.user,
    )
    return {"blueprint_id": str(blueprint.id), "status": blueprint.status.value}


async def validate_agent_blueprint_tool(payload: ValidateAgentBlueprintInput, _: ToolContext) -> dict[str, Any]:
    return validate_agent_blueprint(payload.blueprint)


async def render_workflow_graph_tool(payload: RenderWorkflowGraphInput, _: ToolContext) -> dict[str, Any]:
    return render_workflow_graph(payload.steps)


register_tool(
    AgentToolDefinition(
        name="list_available_tools",
        description="Каталог инструментов платформы",
        agent_description="Возвращает список доступных инструментов агентов платформы",
        handler=list_available_tools,
        input_model=EmptyToolInput,
    )
)
register_tool(
    AgentToolDefinition(
        name="get_tool_description",
        description="Описание инструмента",
        agent_description="Возвращает описание конкретного инструмента по имени",
        handler=get_tool_description,
        input_model=GetToolDescriptionInput,
    )
)
register_tool(
    AgentToolDefinition(
        name="search_agent_templates",
        description="Поиск шаблонов агентов",
        agent_description="Ищет одобренные blueprint агентов как шаблоны",
        handler=search_agent_templates,
        input_model=SearchAgentTemplatesInput,
    )
)
register_tool(
    AgentToolDefinition(
        name="save_agent_blueprint",
        description="Сохранение blueprint агента",
        agent_description="Сохраняет черновик blueprint агента в сессии конструктора",
        handler=save_agent_blueprint,
        input_model=SaveAgentBlueprintInput,
    )
)
register_tool(
    AgentToolDefinition(
        name="validate_agent_blueprint",
        description="Валидация blueprint",
        agent_description="Проверяет полноту blueprint агента",
        handler=validate_agent_blueprint_tool,
        input_model=ValidateAgentBlueprintInput,
    )
)
register_tool(
    AgentToolDefinition(
        name="render_workflow_graph",
        description="Построение workflow graph",
        agent_description="Строит read-only граф workflow по списку шагов",
        handler=render_workflow_graph_tool,
        input_model=RenderWorkflowGraphInput,
    )
)
