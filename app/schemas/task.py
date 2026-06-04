from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from app.models.enums import ConfidenceLevel, FindingSeverity, TaskStatus, TaskStepStatus
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


class TaskResultCreate(BaseModel):
    agent_id: uuid.UUID | None = None
    status: str = "completed"
    conclusion: str | None = None
    summary: str | None = None
    findings: list[dict] = Field(default_factory=list)
    data_confidence: ConfidenceLevel | None = ConfidenceLevel.MEDIUM
    requires_human_review: bool = False
    additional_data: dict | None = None
    report_bucket: str | None = None
    report_object_name: str | None = None
    report_url: str | None = None
    metadata: dict | None = None
    raw_output: dict | None = None


class TaskResultRead(ORMModel):
    id: uuid.UUID
    task_id: uuid.UUID
    agent_id: uuid.UUID | None
    status: str
    conclusion: str | None
    summary: str | None
    findings: list[dict] | None
    data_confidence: ConfidenceLevel | None
    requires_human_review: bool
    additional_data: dict | None
    report_bucket: str | None
    report_object_name: str | None
    report_url: str | None
    is_current: bool
    generated_at: datetime | None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")
    raw_output: dict | None
    created_at: datetime
    updated_at: datetime

class TaskCreate(BaseModel):
    title: str = Field(..., max_length=512)
    description: str | None = None
    agent_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    task_type: str | None = None
    input_payload: dict | None = None
    run_parameters: dict | None = None
    requires_human_review: bool = False
    task_metadata: dict | None = None

class TaskRead(ORMModel):
    id: uuid.UUID
    title: str
    description: str | None
    status: TaskStatus
    agent_id: uuid.UUID | None
    created_by_id: uuid.UUID | None
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    task_type: str | None
    input_payload: dict | None
    run_parameters: dict | None
    celery_task_id: str | None
    requires_human_review: bool
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    task_metadata: dict | None
    created_at: datetime
    updated_at: datetime


class TaskStepRead(ORMModel):
    id: uuid.UUID
    task_id: uuid.UUID
    agent_id: uuid.UUID | None
    step_key: str | None
    title: str | None
    description: str | None
    step_status: TaskStepStatus
    order_index: int
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")

    # Legacy fields kept during transition.
    order: int
    name: str
    status: TaskStatus
    payload: dict | None
    created_at: datetime
    updated_at: datetime


class CeleryDebugRequest(BaseModel):
    message: str = "ping"
    payload: dict | None = None


class CeleryTaskEnqueueResponse(BaseModel):
    celery_task_id: str
    status: str
    queue: str


class CeleryTaskStatusResponse(BaseModel):
    celery_task_id: str
    state: str
    ready: bool
    successful: bool | None = None
    result: dict | list | str | int | float | bool | None = None
    error: str | None = None
