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
    created_at: datetime
    updated_at: datetime
