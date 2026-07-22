"""Загрузка и валидация контрактов данных агента (input/output JSON Schema).

Результат агента формируется строго по образцу структурированного результата платформы
(п. 4.8): agent_id, status, summary, findings[], data_confidence, requires_human_review
(проверки T1, T3). Если ``jsonschema`` недоступен — выполняется мягкая проверка ключей.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:  # jsonschema опционален
    import jsonschema
except Exception:  # pragma: no cover
    jsonschema = None  # type: ignore

REQUIRED_OUTPUT_FIELDS = (
    "agent_id",
    "status",
    "summary",
    "findings",
    "data_confidence",
    "requires_human_review",
)


def load_schema(agent_dir: str | Path, name: str) -> dict[str, Any]:
    """Загружает schemas/<name>.schema.json агента."""

    path = Path(agent_dir) / "schemas" / f"{name}.schema.json"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def validate(instance: dict[str, Any], schema: dict[str, Any]) -> None:
    """Валидирует объект по JSON Schema. Бросает исключение при несоответствии.

    При отсутствии ``jsonschema`` проверяет только наличие required-полей верхнего уровня.
    """

    if jsonschema is not None:
        jsonschema.validate(instance, schema)
        return

    for field in schema.get("required", []):
        if field not in instance:
            raise ValueError(f"Отсутствует обязательное поле: {field}")


def validate_output(instance: dict[str, Any], agent_id: str) -> None:
    """Проверяет результат агента на соответствие образцу платформы (п. 4.8)."""

    for field in REQUIRED_OUTPUT_FIELDS:
        if field not in instance:
            raise ValueError(f"Результат не содержит обязательного поля: {field}")
    if instance["agent_id"] != agent_id:
        raise ValueError(
            f"agent_id должен быть строго '{agent_id}', получено '{instance['agent_id']}'"
        )
