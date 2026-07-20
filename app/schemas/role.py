from __future__ import annotations

import uuid
from datetime import datetime

from app.schemas.common import ORMModel


class RoleRead(ORMModel):
    id: uuid.UUID
    name: str
    code: str
    description: str | None = None
    is_system: bool
    created_at: datetime
    updated_at: datetime
