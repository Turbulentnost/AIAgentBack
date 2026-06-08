from __future__ import annotations

from pydantic import BaseModel

from app.agents.tools.registry import AgentToolDefinition, register_tool


class TaskToolInput(BaseModel):
    task_id: str | None = None
    payload: dict | None = None


def _register_stub(name: str, description: str, agent_description: str) -> None:
    register_tool(
        AgentToolDefinition(
            name=name,
            description=description,
            agent_description=agent_description,
            input_model=TaskToolInput,
            required_permissions=[name],
        )
    )


_register_stub(
    "create_finding",
    "Создает структурированное замечание по результатам анализа.",
    "Инструмент create_finding создает структурированное замечание по результатам анализа. Используй его, когда "
    "выявлено несоответствие, риск, отсутствующее поле, противоречие или необходимость ручной проверки. "
    "Замечание должно содержать тип, критичность, описание, источник и рекомендацию.",
)
_register_stub(
    "request_human_review",
    "Переводит задачу в состояние ручной проверки.",
    "Инструмент request_human_review переводит задачу в состояние ручной проверки. Используй его, если данных "
    "недостаточно, документы повреждены, источник требований не найден, результат имеет низкую уверенность или "
    "требуется решение ответственного сотрудника.",
)
_register_stub(
    "get_current_user_context",
    "Возвращает профиль текущего пользователя и доступные контуры.",
    "Инструмент get_current_user_context возвращает данные пользователя, подразделение, роль и доступные контуры. "
    "Используй его, чтобы определить, какие документы, базы знаний и агенты доступны пользователю. Не используй "
    "этот инструмент для получения чувствительных данных, не нужных для выполнения задачи.",
)
_register_stub(
    "list_available_agents",
    "Возвращает список ИИ-агентов, доступных текущему пользователю.",
    "Инструмент list_available_agents возвращает список ИИ-агентов, доступных текущему пользователю. Используй его, "
    "если нужно предложить пользователю подходящий агент или передать задачу другому специализированному агенту.",
)
