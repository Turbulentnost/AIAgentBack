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
    created_at: datetime
    updated_at: datetime


class AgentAccessRead(AgentRead):
    access_level: str | None = None
    can_run: bool = False
    can_view_results: bool = False
    can_approve: bool = False
    can_configure: bool = False
