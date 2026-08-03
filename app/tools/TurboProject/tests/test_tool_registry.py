import app.tools  # noqa: F401
from app.tools.registry import tool_registry


def test_turbo_project_tools_are_registered() -> None:
    names = {tool.name for tool in tool_registry.list()}

    assert "list_turbo_projects" in names
    assert "get_turbo_project" in names
    assert "get_turbo_project_working_group" in names
    assert tool_registry.get("get_turbo_project_working_group").implemented is True
