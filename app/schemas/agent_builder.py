from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import (
    AgentBlueprintStatus,
    AgentBuilderPlanStatus,
    AgentBuilderPlanStepStatus,
    AgentBuilderSessionStatus,
)


class AgentBuilderSessionCreate(BaseModel):
    goal: str = Field(..., min_length=3, max_length=5000)


class AgentBuilderMessageCreate(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)


class AgentBuilderPlanStepRead(BaseModel):
    id: uuid.UUID
    step_order: int
    title: str
    description: str | None = None
    status: AgentBuilderPlanStepStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error_message: str | None = None

    model_config = {"from_attributes": True}


class AgentBuilderPlanRead(BaseModel):
    id: uuid.UUID
    goal: str
    status: AgentBuilderPlanStatus
    steps: list[AgentBuilderPlanStepRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class AgentBuilderAttemptRead(BaseModel):
    id: uuid.UUID
    attempt_number: int
    goal: str | None = None
    success: bool
    result_summary: str | None = None
    failure_reason: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentBlueprintRead(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    description: str | None = None
    status: AgentBlueprintStatus
    version: int
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    tools: list[Any] | None = None
    knowledge_bases: list[Any] | None = None
    workflow_graph: dict[str, Any] | None = None
    human_approval_rules: list[Any] | None = None
    prompts: dict[str, Any] | None = None
    test_cases: list[Any] | None = None
    report_template: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="metadata_")

    model_config = {"from_attributes": True, "populate_by_name": True}


class AgentBuilderSessionRead(BaseModel):
    id: uuid.UUID
    goal: str
    current_stage: str | None = None
    status: AgentBuilderSessionStatus
    collected_requirements: dict[str, Any] | None = None
    validation_result: dict[str, Any] | None = None
    proposed_agent_structure: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentBuilderDesignStageRead(BaseModel):
    id: str
    label: str
    status: str


class AgentBuilderRequiredElementRead(BaseModel):
    key: str
    label: str
    question: str | None = None
    required: bool = True
    value: str | None = None
    status: str = "pending"


class AgentBuilderPreviewRead(BaseModel):
    success: bool
    preview_type: str | None = None
    output_text: str | None = None
    city: str | None = None
    source: str | None = None
    source_url: str | None = None
    error: str | None = None


class AgentBuilderSessionDetailRead(AgentBuilderSessionRead):
    plan: AgentBuilderPlanRead | None = None
    attempts: list[AgentBuilderAttemptRead] = Field(default_factory=list)
    blueprint: AgentBlueprintRead | None = None
    assistant_messages: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    design_stages: list[AgentBuilderDesignStageRead] = Field(default_factory=list)
    required_elements: list[AgentBuilderRequiredElementRead] = Field(default_factory=list)
    requirements_validation: dict[str, Any] | None = None
    preview_result: AgentBuilderPreviewRead | None = None


class AgentBuilderToolCatalogItem(BaseModel):
    name: str
    description: str
    implemented: bool
    required_permissions: list[str] = Field(default_factory=list)
