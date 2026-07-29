from __future__ import annotations

import uuid

from pydantic import BaseModel


class EskdUserRead(BaseModel):
    id: uuid.UUID
    login: str
    display_name: str
    role: str
    department: str | None = None


class EskdUserListResponse(BaseModel):
    items: list[EskdUserRead]
