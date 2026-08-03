from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class PositionDepartmentRead(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class PositionRead(ORMModel):
    id: uuid.UUID
    name: str
    normalized_name: str
    canonical_key: str
    slug: str
    departments_count: int = 0
    assignments_count: int = 0
    is_active: bool = True
    source_system: str | None = None
    external_id: str | None = None
    created_at: datetime
    updated_at: datetime
    departments: list[PositionDepartmentRead] = Field(default_factory=list)
