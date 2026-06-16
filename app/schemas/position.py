from __future__ import annotations

from pydantic import BaseModel, Field


class PositionRead(BaseModel):
    name: str = Field(..., max_length=255)
