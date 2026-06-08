from __future__ import annotations

from pydantic import BaseModel

from app.agents.tools.registry import AgentToolDefinition, register_tool


class ReportToolInput(BaseModel):
    task_id: str | None = None
    content: dict | None = None


def _register_stub(name: str, description: str, agent_description: str) -> None:
    register_tool(
        AgentToolDefinition(
            name=name,
            description=description,
            agent_description=agent_description,
            input_model=ReportToolInput,
            required_permissions=[name],
        )
    )


_register_stub(
    "save_agent_artifact",
    "Сохраняет промежуточный файл или JSON-результат агента.",
    "Инструмент save_agent_artifact сохраняет промежуточный файл или JSON-результат работы агента в хранилище "
    "артефактов. Используй его для сохранения извлеченных полей, нормализованных таблиц, промежуточных расчетов, "
    "сравнений документов и технических результатов анализа.",
)
_register_stub(
    "generate_docx_report",
    "Формирует DOCX-отчет по результатам работы агента.",
    "Инструмент generate_docx_report формирует DOCX-отчет по результатам работы агента на основании summary, "
    "findings, источников и рекомендаций. Используй его после завершения анализа, когда нужно подготовить "
    "итоговый документ для пользователя.",
)
_register_stub(
    "generate_change_notice",
    "Формирует DOCX-файл извещения об изменении нормативного документа.",
    "Инструмент generate_change_notice используй после подготовки проекта новой редакции и diff. "
    "Извещение должно содержать причину, даты выпуска/введения, содержание изменения, приложения и рассылку.",
)
