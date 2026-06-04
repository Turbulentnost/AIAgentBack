from __future__ import annotations
from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict
T = TypeVar("T")
class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
class HealthResponse(BaseModel):
    status: str = "ok"
    environment: str
    version: str
class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int
