from __future__ import annotations

from pydantic import BaseModel, Field


class BlueprintAgentCard(BaseModel):
    name: str
    purpose: str
    roles: list[str] = Field(default_factory=list)


class WorkflowGraphNode(BaseModel):
    id: str
    label: str
    type: str = "step"


class WorkflowGraphEdge(BaseModel):
    source: str
    target: str
    label: str | None = None


class WorkflowGraph(BaseModel):
    nodes: list[WorkflowGraphNode] = Field(default_factory=list)
    edges: list[WorkflowGraphEdge] = Field(default_factory=list)


class AgentBlueprintPayload(BaseModel):
    agent_card: BlueprintAgentCard
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    knowledge_bases: list[str] = Field(default_factory=list)
    workflow_graph: WorkflowGraph = Field(default_factory=WorkflowGraph)
    human_approval_rules: list[dict] = Field(default_factory=list)
    prompts: dict = Field(default_factory=dict)
    task_statuses: list[str] = Field(default_factory=list)
    finding_schema: dict = Field(default_factory=dict)
    report_template: dict = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    test_cases: list[dict] = Field(default_factory=list)
