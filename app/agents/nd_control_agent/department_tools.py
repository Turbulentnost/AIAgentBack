from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from app.services.onec_departments_fetcher import (
    EnterpriseDepartment,
    fetch_all_departments_from_1c,
    filter_departments,
)
from app.tools.base import Tool
from app.tools.registry import register_tool
from app.tools.schemas import ToolContext


class ListEnterpriseDepartmentsFrom1CInput(BaseModel):
    query: str | None = Field(
        default=None,
        description="Необязательный фильтр по названию или полному пути подразделения",
    )
    limit: int = Field(default=500, ge=1, le=5000)


class ListEnterpriseDepartmentsFrom1COutput(BaseModel):
    total: int
    items: list[EnterpriseDepartment]
    source: str = "1c"
    catalog: str = "Catalog_СтруктураПредприятия"


class ListEnterpriseDepartmentsFrom1CTool(Tool):
    name = "list_enterprise_departments_from_1c"
    description = "Возвращает все активные подразделения из 1С."
    agent_description = (
        "Инструмент list_enterprise_departments_from_1c загружает актуальную структуру предприятия из 1С "
        "(Catalog_СтруктураПредприятия). Используй его, когда нужно заполнить поля карточки документа "
        "«Подразделение-владелец», «Связанные подразделения» или проверить корректность названия подразделения. "
        "Не выдумывай подразделения — бери только из результата инструмента."
    )
    input_model = ListEnterpriseDepartmentsFrom1CInput
    output_model = ListEnterpriseDepartmentsFrom1COutput
    required_permissions = ["list_enterprise_departments_from_1c"]
    preview_safe = True

    async def execute(
        self,
        payload: ListEnterpriseDepartmentsFrom1CInput,
        context: ToolContext,
    ) -> ListEnterpriseDepartmentsFrom1COutput:
        _ = context
        rows = await asyncio.to_thread(fetch_all_departments_from_1c)
        filtered = filter_departments(rows, query=payload.query, limit=payload.limit)
        return ListEnterpriseDepartmentsFrom1COutput(total=len(filtered), items=filtered)


register_tool(ListEnterpriseDepartmentsFrom1CTool())
