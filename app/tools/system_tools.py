from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from app.tools.base import Tool
from app.tools.registry import register_tool
from app.tools.schemas import ToolContext

WEEKDAY_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)

MONTH_RU = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


class GetCurrentDateInput(BaseModel):
    timezone: str = Field(
        default="Europe/Moscow",
        description="IANA timezone, например Europe/Moscow или UTC",
    )


class GetCurrentDateOutput(BaseModel):
    date_iso: str
    date_ru: str
    weekday_ru: str
    timezone: str
    datetime_iso: str


def resolve_current_date(timezone: str = "Europe/Moscow") -> GetCurrentDateOutput:
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Неизвестный timezone: {timezone}") from exc

    now = datetime.now(tz)
    date_ru = f"{now.day} {MONTH_RU[now.month]} {now.year}"
    return GetCurrentDateOutput(
        date_iso=now.date().isoformat(),
        date_ru=date_ru,
        weekday_ru=WEEKDAY_RU[now.weekday()],
        timezone=timezone,
        datetime_iso=now.isoformat(),
    )


async def get_current_date(payload: GetCurrentDateInput, context: ToolContext) -> GetCurrentDateOutput:
    del context
    return resolve_current_date(payload.timezone)


class GetCurrentDateTool(Tool):
    name = "get_current_date"
    description = "Возвращает текущую дату и день недели в указанном часовом поясе."
    agent_description = (
        "Инструмент get_current_date возвращает сегодняшнюю дату, день недели и время "
        "в заданном часовом поясе (по умолчанию Europe/Moscow). "
        "Используй его, когда в задаче фигурирует «сегодня», «на сегодня», «текущая дата» "
        "или нужно определить актуальный день для поиска информации."
    )
    input_model = GetCurrentDateInput
    output_model = GetCurrentDateOutput
    preview_safe = True
    preview_always = True
    preview_default_params = {"timezone": "Europe/Moscow"}

    async def execute(self, payload: GetCurrentDateInput, context: ToolContext) -> GetCurrentDateOutput:
        return await get_current_date(payload, context)


register_tool(GetCurrentDateTool())
