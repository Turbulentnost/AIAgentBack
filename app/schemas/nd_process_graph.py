from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ProcessGraphStepItem(BaseModel):
    name: str
    performer: str | None = None
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


class ProcessGraphDTO(BaseModel):
    process_id: str
    process_name: str
    actors: list[str] = Field(default_factory=list)
    steps: list[ProcessGraphStepItem] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    forms: list[str] = Field(default_factory=list)
    subprocesses: list[ProcessSubprocessItem] = Field(default_factory=list)
