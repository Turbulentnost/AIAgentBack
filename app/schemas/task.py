from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from app.models.enums import ConfidenceLevel, FindingSeverity, TaskStatus
from app.schemas.common import ORMModel
class Finding(BaseModel):
    type: str
    severity: FindingSeverity
    description: str
    source: str | None = None
    recommendation: str | None = None
class AgentResult(BaseModel):
    agent_id: str
    status: str
    summary: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    data_confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    requires_human_review: bool = False
class TaskCreate(BaseModel):
    title: str = Field(..., max_length=512)
    description: str | None = None
    task_type: str | None = None
    input_payload: dict | None = None
class TaskRead(ORMModel):
    id: uuid.UUID
    title: str
    description: str | None
    status: TaskStatus
    task_type: str | None
    requires_human_review: bool
    final_result: dict | None
    created_at: datetime
    updated_at: datetime
