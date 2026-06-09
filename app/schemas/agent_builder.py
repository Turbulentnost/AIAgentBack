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
    AgentType,
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


class AgentTypeProposalRead(BaseModel):
    proposed_agent_type: str | None = None
    confidence: float | None = None
    reasoning: str | None = None
    confirmed: bool = False


class AgentBlueprintRead(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    description: str | None = None
    agent_type: str | None = None
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


class AgentBuilderDesignSummaryRead(BaseModel):
    """Статическая сводка структуры агента (без выполнения инструментов)."""

    success: bool
    summary_type: str | None = None
    output_text: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    runtime_dependencies: list[str] = Field(default_factory=list)
    input_params: list[str] = Field(default_factory=list)
    output_format: list[str] = Field(default_factory=list)
    valid: bool = True
    errors: list[str] = Field(default_factory=list)


class AgentBuilderSessionDetailRead(AgentBuilderSessionRead):
    plan: AgentBuilderPlanRead | None = None
    attempts: list[AgentBuilderAttemptRead] = Field(default_factory=list)
    blueprint: AgentBlueprintRead | None = None
    assistant_messages: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    design_stages: list[AgentBuilderDesignStageRead] = Field(default_factory=list)
    required_elements: list[AgentBuilderRequiredElementRead] = Field(default_factory=list)
    requirements_validation: dict[str, Any] | None = None
    design_summary: AgentBuilderDesignSummaryRead | None = None
    agent_type: str | None = None
    agent_type_proposal: AgentTypeProposalRead | None = None


class AgentBuilderToolCatalogItem(BaseModel):
    name: str
    description: str
    implemented: bool
    required_permissions: list[str] = Field(default_factory=list)


class SandboxStepRead(BaseModel):
    id: uuid.UUID
    order_index: int
    title: str | None = None
    capability: str | None = None
    tool_name: str | None = None
    status: str
    request: dict[str, Any] | None = None
    result_summary: dict[str, Any] | None = None
    duration_ms: int | None = None
    error_message: str | None = None

    model_config = {"from_attributes": True}


class SandboxRunStartCreate(BaseModel):
    test_query: str | None = None


class SandboxRunRead(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    status: str
    test_query: str | None = None
    final_answer: str | None = None
    stats: dict[str, Any] | None = None
    executed_graph: dict[str, Any] | None = None
    error_message: str | None = None
    steps: list[SandboxStepRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}
