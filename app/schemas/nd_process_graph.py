from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.diagram_block import DiagramBlockType
from app.schemas.process_smk_sections import (
    ProcessApplicationItem,
    ProcessChangeRegistrationItem,
    ProcessDocumentationArchiveItem,
    ProcessEffectivenessCriterionItem,
    ProcessIssueAcquaintanceItem,
    ProcessResourceItem,
    ProcessRiskItem,
)


class ProcessGraphActionItem(BaseModel):
    id: str
    title: str
    description: str | None = None
    responsible_role: str | None = None
    input_objects: list[str] = Field(default_factory=list)
    output_objects: list[str] = Field(default_factory=list)
    used_forms: list[str] = Field(default_factory=list)
    used_systems: list[str] = Field(default_factory=list)
    related_document_sections: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    block_type: DiagramBlockType = DiagramBlockType.OPERATION
    graph_item_kind: Literal["flow_step", "document_artifact"] = "flow_step"
    controller: str | None = None
    system_or_resource: str | None = None


class ProcessSubprocessItem(BaseModel):
    process_id: str | None = None
    name: str
    relation_type: str
    relation_type_label: str
    direction: str
    actors: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    forms: list[str] = Field(default_factory=list)


class ProcessExternalReferenceItem(BaseModel):
    name: str
    reference_type: str
    relation_type_label: str | None = None
    evidence: list[dict] = Field(default_factory=list)


class ProcessGraphMetadataItem(BaseModel):
    name: str
    category: str
    graph_item_kind: Literal["metadata", "reference"] = "metadata"
    payload: dict[str, Any] = Field(default_factory=dict)


class ProcessSourceDocumentTypeItem(BaseModel):
    document_type: str
    document_type_label: str | None = None
    qms_level: str | None = None
    qms_level_label: str | None = None
    document_name: str | None = None


class ProcessGraphDTO(BaseModel):
    process_id: str
    process_name: str
    process_goal: str | None = None
    process_owner: str | None = None
    source_document_type: str | None = None
    source_document_type_label: str | None = None
    qms_level: str | None = None
    qms_level_label: str | None = None
    primary_document_type: str | None = None
    source_document_types: list[ProcessSourceDocumentTypeItem] = Field(default_factory=list)
    diagram_profile_label: str | None = None
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    actions: list[ProcessGraphActionItem] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    forms: list[str] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)
    resources: list[ProcessResourceItem] = Field(default_factory=list)
    risks: list[ProcessRiskItem] = Field(default_factory=list)
    effectiveness_criteria: list[ProcessEffectivenessCriterionItem] = Field(default_factory=list)
    documentation_and_archive: list[ProcessDocumentationArchiveItem] = Field(default_factory=list)
    applications: list[ProcessApplicationItem] = Field(default_factory=list)
    change_registration: list[ProcessChangeRegistrationItem] = Field(default_factory=list)
    issue_and_acquaintance: list[ProcessIssueAcquaintanceItem] = Field(default_factory=list)
    storage_locations: list[str] = Field(default_factory=list)
    retention_terms: list[str] = Field(default_factory=list)
    responsible_for_storage: list[str] = Field(default_factory=list)
    measurement_methods: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    subprocesses: list[ProcessSubprocessItem] = Field(default_factory=list)
    external_references: list[ProcessExternalReferenceItem] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    process_metadata: dict[str, list[ProcessGraphMetadataItem]] = Field(default_factory=dict)


class ProcessUmlSchemaComposition(BaseModel):
    start_end: int = 0
    operations: int = 0
    decisions: int = 0
    documents: int = 0
    roles: int = 0
    forms: int = 0
    systems: int = 0
    related_processes: int = 0
    effectiveness_criteria: int = 0
    resources: int = 0
    risks: int = 0
    archive_items: int = 0


def build_schema_composition(graph: ProcessGraphDTO) -> ProcessUmlSchemaComposition:
    start_end = sum(
        1
        for item in graph.actions
        if item.block_type in {DiagramBlockType.START, DiagramBlockType.END}
    )
    operations = sum(1 for item in graph.actions if item.block_type == DiagramBlockType.OPERATION)
    decisions = sum(1 for item in graph.actions if item.block_type == DiagramBlockType.DECISION)
    documents = sum(
        1
        for item in graph.actions
        if item.block_type in {DiagramBlockType.DOCUMENT_OUTPUT, DiagramBlockType.SUBPROCESS}
    )
    return ProcessUmlSchemaComposition(
        start_end=start_end,
        operations=operations,
        decisions=decisions,
        documents=documents + len(graph.documents) + len(graph.applications),
        roles=len(graph.roles),
        forms=len(graph.forms),
        systems=len(graph.systems),
        related_processes=len(graph.subprocesses),
        effectiveness_criteria=len(graph.effectiveness_criteria),
        resources=len(graph.resources),
        risks=len(graph.risks),
        archive_items=len(graph.documentation_and_archive),
    )


ProcessGraphStepItem = ProcessGraphActionItem
