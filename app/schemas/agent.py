from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from app.models.enums import AgentStatus
from app.schemas.common import ORMModel
class AgentBase(BaseModel):
    name: str = Field(..., max_length=255)
    slug: str = Field(..., max_length=128)
    purpose: str | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None
    department_id: uuid.UUID | None = None
class AgentCreate(AgentBase):
    pass
class AgentUpdate(BaseModel):
    name: str | None = None
    purpose: str | None = None
    status: AgentStatus | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None
class AgentRead(AgentBase, ORMModel):
    id: uuid.UUID
    status: AgentStatus
    owner_id: uuid.UUID | None = None
    icon_url: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentAccessRead(AgentRead):
    access_level: str | None = None
    can_run: bool = False
    can_view_results: bool = False
    can_approve: bool = False
    can_configure: bool = False


class AgentDepartmentGrantRead(ORMModel):
    id: uuid.UUID
    department_id: uuid.UUID
    access_level: str
    can_run: bool
    can_view_results: bool
    can_approve: bool
    can_configure: bool


class AgentUserGrantRead(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    access_level: str
    can_run: bool
    can_view_results: bool
    can_approve: bool
    can_configure: bool
    expires_at: datetime | None = None


class AgentAccessManagementRead(BaseModel):
    department_grants: list[AgentDepartmentGrantRead]
    user_grants: list[AgentUserGrantRead]


class AgentDepartmentGrantInput(BaseModel):
    department_id: uuid.UUID
    access_level: str = Field(default="run", max_length=64)
    can_run: bool = True
    can_view_results: bool = True
    can_approve: bool = False
    can_configure: bool = False


class AgentUserGrantInput(BaseModel):
    user_id: uuid.UUID
    access_level: str = Field(default="run", max_length=64)
    can_run: bool = True
    can_view_results: bool = True
    can_approve: bool = False
    can_configure: bool = False
    expires_at: datetime | None = None


class AgentAccessUpdate(BaseModel):
    department_grants: list[AgentDepartmentGrantInput] = Field(default_factory=list)
    user_grants: list[AgentUserGrantInput] = Field(default_factory=list)
