from __future__ import annotations
from pydantic import BaseModel
from app.schemas.task import AgentResult, Finding
class BaseAgentInput(BaseModel):
    task_id: str
    document_ids: list[str] = []
    user_id: str | None = None
    params: dict | None = None
__all__ = ["AgentResult", "Finding", "BaseAgentInput"]
