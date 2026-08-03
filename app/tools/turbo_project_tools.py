from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from app.tools.base import Tool
from app.tools.registry import register_tool
from app.tools.schemas import ToolContext
from app.tools.TurboProject.projects import get_turbo_project, list_turbo_projects
from app.tools.TurboProject.working_group import get_turbo_project_working_group


class TurboProjectSummaryItem(BaseModel):
    file_id: int | None = None
    original_name: str | None = None
    uploaded_at: str | None = None
    project_name: str | None = None
    has_1c: bool = False


class ListTurboProjectsInput(BaseModel):
    only_with_1c: bool = Field(
        default=True,
        description="Вернуть только проекты с синхронизацией 1С",
    )
    query: str | None = Field(
        default=None,
        description="Фильтр по названию проекта (подстрока, без учёта регистра)",
    )
    include_details: bool = Field(
        default=False,
        description="Загрузить полные детали каждого проекта (медленнее)",
    )


class ListTurboProjectsOutput(BaseModel):
    total_projects: int
    projects_with_1c_count: int
    matched_count: int
    generated_at: str
    projects: list[dict[str, Any]]


async def list_turbo_projects_tool(
    payload: ListTurboProjectsInput,
    context: ToolContext,
) -> ListTurboProjectsOutput:
    del context
    raw = await asyncio.to_thread(
        list_turbo_projects,
        only_with_1c=payload.only_with_1c,
        query=payload.query,
        include_details=payload.include_details,
    )
    return ListTurboProjectsOutput.model_validate(raw)


class ListTurboProjectsTool(Tool):
    name = "list_turbo_projects"
    description = "Возвращает список проектов из TurboProject (MS Project + 1С)."
    agent_description = (
        "Инструмент list_turbo_projects читает проекты из TurboProject API. "
        "only_with_1c=true — только проекты с синхронизацией 1С; query — фильтр по названию. "
        "include_details=true — полная карточка каждого проекта. "
        "Нужны TURBO_PROJECT_API_BASE_URL, TURBO_PROJECT_EMAIL, TURBO_PROJECT_PASSWORD в .env."
    )
    input_model = ListTurboProjectsInput
    output_model = ListTurboProjectsOutput
    required_permissions = ["list_turbo_projects"]
    preview_default_params = {"only_with_1c": True, "query": None, "include_details": False}

    async def execute(
        self,
        payload: ListTurboProjectsInput,
        context: ToolContext,
    ) -> ListTurboProjectsOutput:
        return await list_turbo_projects_tool(payload, context)


register_tool(ListTurboProjectsTool())


class GetTurboProjectInput(BaseModel):
    file_id: int | None = Field(default=None, description="ID файла проекта в TurboProject")
    project_name: str | None = Field(default=None, description="Название или часть названия проекта")
    one_c_ref_key: str | None = Field(default=None, description="GUID проекта в 1С")


class GetTurboProjectOutput(BaseModel):
    file_id: int | None = None
    original_name: str | None = None
    uploaded_at: str | None = None
    project_name: str | None = None
    has_1c: bool = False
    dates: dict[str, Any] = Field(default_factory=dict)
    task_stats: dict[str, Any] = Field(default_factory=dict)
    overdue_tasks: list[dict[str, Any]] = Field(default_factory=list)
    overdue_milestones: list[dict[str, Any]] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    data_1c: dict[str, Any] | None = None


async def get_turbo_project_tool(
    payload: GetTurboProjectInput,
    context: ToolContext,
) -> GetTurboProjectOutput:
    del context
    raw = await asyncio.to_thread(
        get_turbo_project,
        file_id=payload.file_id,
        project_name=payload.project_name,
        one_c_ref_key=payload.one_c_ref_key,
    )
    return GetTurboProjectOutput.model_validate(raw)


class GetTurboProjectTool(Tool):
    name = "get_turbo_project"
    description = "Возвращает карточку проекта TurboProject с задачами, ресурсами и данными 1С."
    agent_description = (
        "Инструмент get_turbo_project возвращает детали проекта TurboProject: даты, задачи, "
        "ресурсы MS Project и блок data_1c. Нужен file_id, project_name или one_c_ref_key. "
        "Нужны TURBO_PROJECT_* в .env."
    )
    input_model = GetTurboProjectInput
    output_model = GetTurboProjectOutput
    required_permissions = ["get_turbo_project"]
    preview_default_params = {"project_name": "Turbo"}

    async def execute(
        self,
        payload: GetTurboProjectInput,
        context: ToolContext,
    ) -> GetTurboProjectOutput:
        return await get_turbo_project_tool(payload, context)


register_tool(GetTurboProjectTool())


class TurboProjectWorkingGroupMember(BaseModel):
    fio: str
    role: str
    source: str


class GetTurboProjectWorkingGroupInput(BaseModel):
    file_id: int | None = Field(default=None, description="ID файла проекта в TurboProject")
    project_name: str | None = Field(default=None, description="Название или часть названия проекта")
    one_c_ref_key: str | None = Field(default=None, description="GUID проекта в 1С")


class GetTurboProjectWorkingGroupOutput(BaseModel):
    file_id: int | None = None
    project_name: str | None = None
    one_c_ref_key: str | None = None
    members_count: int
    member_fios: list[str]
    members: list[TurboProjectWorkingGroupMember]
    resources: list[str]
    project: dict[str, Any] = Field(default_factory=dict)


async def get_turbo_project_working_group_tool(
    payload: GetTurboProjectWorkingGroupInput,
    context: ToolContext,
) -> GetTurboProjectWorkingGroupOutput:
    del context
    raw = await asyncio.to_thread(
        get_turbo_project_working_group,
        file_id=payload.file_id,
        project_name=payload.project_name,
        one_c_ref_key=payload.one_c_ref_key,
    )
    return GetTurboProjectWorkingGroupOutput.model_validate(raw)


class GetTurboProjectWorkingGroupTool(Tool):
    name = "get_turbo_project_working_group"
    description = (
        "Возвращает рабочую группу проекта TurboProject: роли из 1С и ресурсы MS Project."
    )
    agent_description = (
        "Инструмент get_turbo_project_working_group собирает участников рабочей группы проекта "
        "для серии совещаний: руководитель, куратор, заказчик, инвестор, зам. РП из 1С и ресурсы "
        "MS Project. Нужен file_id, project_name или one_c_ref_key. Нужны TURBO_PROJECT_* в .env."
    )
    input_model = GetTurboProjectWorkingGroupInput
    output_model = GetTurboProjectWorkingGroupOutput
    required_permissions = ["get_turbo_project_working_group"]
    preview_default_params = {"project_name": "Turbo"}

    async def execute(
        self,
        payload: GetTurboProjectWorkingGroupInput,
        context: ToolContext,
    ) -> GetTurboProjectWorkingGroupOutput:
        return await get_turbo_project_working_group_tool(payload, context)


register_tool(GetTurboProjectWorkingGroupTool())
