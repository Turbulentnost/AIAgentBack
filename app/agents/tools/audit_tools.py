from __future__ import annotations

from pydantic import BaseModel

from app.agents.tools.registry import AgentToolDefinition, register_tool


class AuditToolInput(BaseModel):
    resource_id: str | None = None
    action: str | None = None


register_tool(
    AgentToolDefinition(
        name="list_agent_audit_events",
        description="Возвращает аудит действий агента по задаче или ресурсу.",
        agent_description=(
            "Инструмент list_agent_audit_events возвращает журнал действий агента, вызовов инструментов и "
            "использованных источников. Используй его для проверки воспроизводимости вывода, расследования ошибок "
            "и подготовки объяснения пользователю."
        ),
        input_model=AuditToolInput,
        required_permissions=["audit.read_agent_events"],
    )
)
