from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class DepartmentCreate(BaseModel):
    name: str = Field(..., max_length=255)
    slug: str = Field(..., max_length=128)
    description: str | None = None
    parent_id: uuid.UUID | None = None
    is_active: bool = True


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    slug: str | None = Field(default=None, max_length=128)
    description: str | None = None
    parent_id: uuid.UUID | None = None
    is_active: bool | None = None


class DepartmentRead(DepartmentCreate, ORMModel):
    id: uuid.UUID
    source_system: str | None = None
    external_id: str | None = None
    created_at: datetime
    updated_at: datetime


class DepartmentSyncStatus(ORMModel):
    key: str
    source_system: str
    resource: str
    last_synced_at: datetime | None
    next_allowed_at: datetime | None
    status: str
    items_count: int
    error_message: str | None
    payload: dict | None = None


class DepartmentSyncResult(DepartmentSyncStatus):
    created_count: int = 0
    updated_count: int = 0
    deactivated_count: int = 0
    synced_count: int = 0


class DepartmentMemberRead(BaseModel):
    """Краткая карточка пользователя для выбора участников."""

    id: uuid.UUID
    email: str
    username: str | None = None
    last_name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    full_name: str | None = None
    phone: str | None = None
    position: str | None = None
    department_id: uuid.UUID | None = None
    is_active: bool = True

    model_config = {"from_attributes": True}


class DepartmentTreeNode(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    parent_id: uuid.UUID | None = None
    is_active: bool = True
    source_system: str | None = None
    external_id: str | None = None
    members: list[DepartmentMemberRead] = Field(default_factory=list)
    member_count: int = 0
    total_member_count: int = 0
    children: list["DepartmentTreeNode"] = Field(default_factory=list)


class DepartmentTreeResponse(BaseModel):
    """Иерархия подразделений с участниками и плоским списком для фильтрации на фронте."""

    roots: list[DepartmentTreeNode] = Field(default_factory=list)
    members: list[DepartmentMemberRead] = Field(default_factory=list)
    unassigned_members: list[DepartmentMemberRead] = Field(default_factory=list)
    total_departments: int = 0
    total_members: int = 0


DepartmentTreeNode.model_rebuild()
