"""Pydantic schemas for OTK worker presentation cards (MVP)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.quality_control_agent.schemas import QualitySampleRule, TmcCategory

OtkPresentationStatus = Literal["queued", "in_progress", "done"]


class OtkWorkerRead(BaseModel):
    id: str
    name: str
    position: str


class OtkShipmentLineBase(BaseModel):
    code: str = ""
    nomenclature: str = ""
    storage_unit: str = "шт"
    qty_upd: float = 0
    qty_fact: float = 0
    category: TmcCategory | str = "other"
    supplier_quality_rating: str | float | int | None = None


class OtkShipmentLineCreate(OtkShipmentLineBase):
    pass


class OtkShipmentLineUpdate(BaseModel):
    code: str | None = None
    nomenclature: str | None = None
    storage_unit: str | None = None
    qty_upd: float | None = None
    qty_fact: float | None = None
    category: TmcCategory | str | None = None
    supplier_quality_rating: str | float | int | None = None


class OtkShipmentLineRead(OtkShipmentLineBase):
    id: str
    sample_rule: QualitySampleRule | None = None


class OtkPresentationSummary(BaseModel):
    id: str
    organization: str
    purchase_order: str
    supplier: str
    invoice_number: str
    due_at: str
    status: OtkPresentationStatus
    lines_count: int = 0
    executor_id: str = ""


class OtkPresentationCardRead(BaseModel):
    id: str
    organization: str
    purchase_order: str
    supplier: str
    counterparty: str
    warehouse: str
    invoice_date: str
    invoice_number: str
    storage_zone: str
    presentation_place: str
    otk_incoming_warehouse: str
    executor_id: str
    due_at: str
    status: OtkPresentationStatus
    lines: list[OtkShipmentLineRead] = Field(default_factory=list)


class OtkPresentationUpdate(BaseModel):
    organization: str | None = None
    purchase_order: str | None = None
    supplier: str | None = None
    counterparty: str | None = None
    warehouse: str | None = None
    invoice_date: str | None = None
    invoice_number: str | None = None
    storage_zone: str | None = None
    presentation_place: str | None = None
    otk_incoming_warehouse: str | None = None
    executor_id: str | None = None
    due_at: str | None = None
    status: OtkPresentationStatus | None = None


class OtkPresentationListResponse(BaseModel):
    items: list[OtkPresentationSummary]
    pending_count: int
    earliest_due_at: str | None = None
    workers: list[OtkWorkerRead] = Field(default_factory=list)


class OtkWriteTo1CResult(BaseModel):
    ok: bool = True
    stub: bool = True
    message: str
    presentation_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "OtkPresentationCardRead",
    "OtkPresentationListResponse",
    "OtkPresentationSummary",
    "OtkPresentationUpdate",
    "OtkShipmentLineCreate",
    "OtkShipmentLineRead",
    "OtkShipmentLineUpdate",
    "OtkWorkerRead",
    "OtkWriteTo1CResult",
]
