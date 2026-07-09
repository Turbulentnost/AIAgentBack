"""Модели детерминированной маршрутизации (ТЗ §8–12)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ConfidenceLevel(StrEnum):
    HIGH = "ВЫСОКАЯ"
    MEDIUM = "СРЕДНЯЯ"
    LOW = "НИЗКАЯ"


class ServiceRoute(BaseModel):
    code: str
    name: str
    process: str = "исполнение"
    reasoning: str = ""
    direction: str = "КС"


class RoutingDecision(BaseModel):
    organization: str = "НП"
    direction: str = "КС"
    process: str = "исполнение"
    services: list[ServiceRoute] = Field(default_factory=list)
    confidence_level: ConfidenceLevel = ConfidenceLevel.LOW
    confidence_score: int = 0
    matching_keywords: list[str] = Field(default_factory=list)
    partner: str | None = None
    claim: bool = False
    theme: str = ""
    has_conflict: bool = False
    match_source: str = ""
    xml_document: str | None = None
