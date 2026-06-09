from __future__ import annotations

from typing import Any


def _element_has_value(item: dict[str, Any]) -> bool:
    value = item.get("value")
    return bool(value and str(value).strip())


def _element_satisfied(item: dict[str, Any]) -> bool:
    if item.get("auto_resolved"):
        return True
    if _element_has_value(item):
        return True
    from app.agents.builder.templates.consultant import CONSULTANT_CONFIDENCE_THRESHOLD

    confidence = item.get("confidence")
    if confidence is not None and float(confidence) >= CONSULTANT_CONFIDENCE_THRESHOLD:
        return True
    return False


REQUIRED_BLUEPRINT_SECTIONS = (
    "agent_card",
    "input_schema",
    "output_schema",
    "tools",
    "workflow_graph",
    "prompts",
)


def validate_agent_blueprint(blueprint: dict[str, Any] | None) -> dict[str, Any]:
    if not blueprint:
        return {"valid": False, "errors": ["Blueprint отсутствует"], "warnings": []}

    errors: list[str] = []
    warnings: list[str] = []

    for section in REQUIRED_BLUEPRINT_SECTIONS:
        value = blueprint.get(section)
        if value is None or value == {} or value == []:
            errors.append(f"Отсутствует обязательная секция: {section}")

    agent_card = blueprint.get("agent_card") or {}
    if not agent_card.get("name"):
        errors.append("agent_card.name обязателен")
    if not agent_card.get("purpose"):
        errors.append("agent_card.purpose обязателен")

    workflow = blueprint.get("workflow_graph") or {}
    nodes = workflow.get("nodes") or []
    edges = workflow.get("edges") or []
    if not nodes:
        errors.append("workflow_graph.nodes не может быть пустым")
    if len(nodes) > 1 and not edges:
        warnings.append("workflow_graph.edges пуст при нескольких узлах")

    from app.agents.tools.registry import agent_tool_registry

    tool_names = {tool.name for tool in agent_tool_registry.list()}
    for node in nodes:
        if node.get("type") in {"start", "end"}:
            continue
        capability = node.get("capability")
        goal = node.get("goal")
        node_id = str(node.get("id") or "")
        if not capability and not goal:
            errors.append(f"Узел workflow '{node.get('label')}' должен иметь capability или goal")
        if capability and capability in tool_names and not goal:
            warnings.append(
                f"Узел '{node.get('label')}' использует имя tool как capability — используйте capability id"
            )

    prompts = blueprint.get("prompts") or {}
    if not prompts.get("system"):
        warnings.append("prompts.system не задан")

    tools = blueprint.get("tools") or []
    if not tools:
        warnings.append("Список tools пуст")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def validate_required_elements(requirements: dict[str, Any] | None) -> dict[str, Any]:
    if not requirements:
        return {"valid": False, "errors": ["Требования не собраны"], "missing": [], "elements": []}

    elements = requirements.get("required_elements") or []
    if elements:
        missing = [
            item.get("label") or item.get("key")
            for item in elements
            if item.get("required", True) and not _element_satisfied(item)
        ]
        return {
            "valid": len(missing) == 0,
            "errors": [f"Не заполнены элементы: {', '.join(missing)}"] if missing else [],
            "missing": missing,
            "elements": elements,
        }

    legacy_missing: list[str] = []
    if "inputs" not in requirements:
        legacy_missing.append("входные данные")
    if "outputs" not in requirements:
        legacy_missing.append("ожидаемый результат")
    if "human_approval" not in requirements:
        legacy_missing.append("согласование с человеком")
    if requirements.get("knowledge_bases_answered") is not True and not requirements.get("knowledge_bases"):
        legacy_missing.append("базы знаний")

    return {
        "valid": len(legacy_missing) == 0,
        "errors": [f"Не заполнены: {', '.join(legacy_missing)}"] if legacy_missing else [],
        "missing": legacy_missing,
        "elements": [],
    }
