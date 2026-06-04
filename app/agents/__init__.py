from app.agents.common.registry import agent_registry
from app.agents.nd_control_agent import service as _nd_control_service  # noqa: F401,E402
from app.agents.task_compliting_agent import service as _task_compliting_service  # noqa: F401,E402

__all__ = ["agent_registry"]
