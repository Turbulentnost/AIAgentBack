"""Pydantic schemas for integration API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class UnifiedDocumentCard(BaseModel):
    document_id: str
    source_system: str = "manual"
    designation: str | None = None
    document_type: str | None = None
    name: str | None = None
    revision: str | None = None
    sheet_count: int | None = None
    author: str | None = None
    department: str | None = None
    product_id: str | None = None
    files: list[dict[str, Any]] = Field(default_factory=list)
    related_documents: list[dict[str, Any]] = Field(default_factory=list)
    status: str | None = None
    checksum: str | None = None
    submitted_at: datetime | None = None
    metadata_extra: dict[str, Any] = Field(default_factory=dict)


class CheckCreateRequest(BaseModel):
    request_id: str | None = None
    document: UnifiedDocumentCard
    ruleset_version: str | None = None
    run_ai: bool = True


class CheckSummaryResponse(BaseModel):
    check_id: uuid.UUID
    request_id: str
    document_id: str | None = None
    designation: str | None = None
    revision: str | None = None
    status: str
    result_status: str | None = None
    critical_count: int = 0
    major_count: int = 0
    minor_count: int = 0
    blocks_workflow: bool = False
    ruleset_version: str | None = None
    report_url: str | None = None
    report_json_url: str | None = None
    checked_at: datetime | None = None
    is_stale: bool = False
    source: str | None = None


class FindingItem(BaseModel):
    page: int
    severity: str
    code: str
    message: str
    gost_reference: str | None = None


class FindingsResponse(BaseModel):
    check_id: uuid.UUID
    items: list[FindingItem]
    total: int


class RulesetInfo(BaseModel):
    version: str
    title: str
    effective_from: str | None = None


class RulesetsResponse(BaseModel):
    items: list[RulesetInfo]
    current: str


class WebhookCreate(BaseModel):
    name: str
    url: str
    events: list[str] = Field(default_factory=lambda: ["CheckCompleted", "CheckRejected"])
    secret: str | None = None
    source_system: str | None = None


class WebhookRead(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    events: list[str]
    enabled: bool
    source_system: str | None = None


class ExchangeLogItem(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    sender: str
    receiver: str
    request_id: str | None = None
    operation: str
    result: str
    error_message: str | None = None
    designation: str | None = None
    revision: str | None = None
    actor: str | None = None


class ExchangeLogListResponse(BaseModel):
    items: list[ExchangeLogItem]
    total: int


class ErpReadinessUpdate(BaseModel):
    document_id: str
    source_system: str = "1c"
    nomenclature_code: str | None = None
    order_number: str | None = None
    project: str | None = None
    department: str | None = None
    due_date: str | None = None
    pdm_link: str | None = None


class ErpReadinessResponse(BaseModel):
    document_id: str
    check_id: uuid.UUID | None = None
    production_ready: bool
    readiness_status: str
    critical_count: int
    report_url: str | None = None
    assignee: str | None = None
    rework_deadline: str | None = None


class SedArchivePayload(BaseModel):
    document_id: str
    source_system: str
    revision: str | None = None
    check_id: uuid.UUID
    decision: str | None = None
    signature_info: dict[str, Any] | None = None


class SedArchiveResponse(BaseModel):
    archived: bool
    archive_ref: str
    checksum: str | None = None
    ruleset_version: str | None = None


class ApiKeyCreateResponse(BaseModel):
    id: uuid.UUID
    name: str
    api_key: str
    roles: list[str]
