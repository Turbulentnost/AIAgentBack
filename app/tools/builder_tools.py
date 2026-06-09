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
from app.tools.base import Tool
from app.tools.registry import register_tool
from app.tools.schemas import EmptyToolInput, ToolContext
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


class ListAvailableToolsTool(Tool):
    name = "list_available_tools"
    description = "Каталог инструментов платформы"
    agent_description = "Возвращает список доступных инструментов агентов платформы"
    input_model = EmptyToolInput

    async def execute(self, payload: EmptyToolInput, context: ToolContext) -> dict[str, Any]:
        return await list_available_tools(payload, context)


class GetToolDescriptionTool(Tool):
    name = "get_tool_description"
    description = "Описание инструмента"
    agent_description = "Возвращает описание конкретного инструмента по имени"
    input_model = GetToolDescriptionInput

    async def execute(self, payload: GetToolDescriptionInput, context: ToolContext) -> dict[str, Any]:
        return await get_tool_description(payload, context)


class SearchAgentTemplatesTool(Tool):
    name = "search_agent_templates"
    description = "Поиск шаблонов агентов"
    agent_description = "Ищет одобренные blueprint агентов как шаблоны"
    input_model = SearchAgentTemplatesInput

    async def execute(self, payload: SearchAgentTemplatesInput, context: ToolContext) -> dict[str, Any]:
        return await search_agent_templates(payload, context)


class SaveAgentBlueprintTool(Tool):
    name = "save_agent_blueprint"
    description = "Сохранение blueprint агента"
    agent_description = "Сохраняет черновик blueprint агента в сессии конструктора"
    input_model = SaveAgentBlueprintInput

    async def execute(self, payload: SaveAgentBlueprintInput, context: ToolContext) -> dict[str, Any]:
        return await save_agent_blueprint(payload, context)


class ValidateAgentBlueprintTool(Tool):
    name = "validate_agent_blueprint"
    description = "Валидация blueprint"
    agent_description = "Проверяет полноту blueprint агента"
    input_model = ValidateAgentBlueprintInput

    async def execute(self, payload: ValidateAgentBlueprintInput, context: ToolContext) -> dict[str, Any]:
        return await validate_agent_blueprint_tool(payload, context)


class RenderWorkflowGraphTool(Tool):
    name = "render_workflow_graph"
    description = "Построение workflow graph"
    agent_description = "Строит read-only граф workflow по списку шагов"
    input_model = RenderWorkflowGraphInput

    async def execute(self, payload: RenderWorkflowGraphInput, context: ToolContext) -> dict[str, Any]:
        return await render_workflow_graph_tool(payload, context)


register_tool(ListAvailableToolsTool())
register_tool(GetToolDescriptionTool())
register_tool(SearchAgentTemplatesTool())
register_tool(SaveAgentBlueprintTool())
register_tool(ValidateAgentBlueprintTool())
register_tool(RenderWorkflowGraphTool())
