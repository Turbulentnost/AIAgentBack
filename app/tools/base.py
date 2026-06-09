from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.tools.schemas import ToolContext, ToolDescriptor


class Tool:
    """Базовый класс для всех инструментов платформы.

    Каждый инструмент — подкласс Tool: задаёт метаданные через атрибуты класса
    (name, description, agent_description, input/output модели, права, preview-флаги)
    и реализует асинхронный метод execute(payload, context).
    Инструменты-заглушки используют StubTool с implemented=False.
    """

    name: str = ""
    description: str = ""
    agent_description: str = ""
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None
    required_permissions: list[str] = []
    preview_safe: bool = False
    preview_always: bool = False
    preview_default_params: dict[str, Any] = {}
    implemented: bool = True

    def __init__(self, **overrides: Any) -> None:
        for key, value in overrides.items():
            setattr(self, key, value)
        if not getattr(self, "name", ""):
            raise ValueError("Tool требует непустое имя name")

    async def execute(self, payload: BaseModel, context: ToolContext) -> Any:
        raise NotImplementedError(f"Инструмент '{self.name}' не реализован")

    @property
    def input_schema(self) -> dict[str, Any] | None:
        return self.input_model.model_json_schema() if self.input_model is not None else None

    @property
    def output_schema(self) -> dict[str, Any] | None:
        return self.output_model.model_json_schema() if self.output_model is not None else None

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description=self.description,
            agent_description=self.agent_description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            required_permissions=list(self.required_permissions),
            implemented=self.implemented,
        )


class StubTool(Tool):
    """Зарегистрированный, но ещё не реализованный инструмент."""

    implemented = False
