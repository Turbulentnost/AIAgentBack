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
