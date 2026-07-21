"""Доменные модели: письмо, вложения, результаты узлов графа."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Priority(StrEnum):
    """Приоритет по таблице G.1 типового справочника (+ СТО-34-238 п. 6.2).

    urgent — немедленно (госорганы, суды, надзор);
    high — 1-я очередь / в день поступления (претензии, обязательства, срок ответа);
    normal — в день регистрации / в течение раб. дня / 2-я очередь (учёт без обязательств).
    """

    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"


class ProcessingStatus(StrEnum):
    PROCESSING = "processing"
    DONE = "done"
    SPAM = "spam"
    ERROR = "error"
    AWAITING_HUMAN = "awaiting_human"
    DIALOG = "dialog"


class Attachment(BaseModel):
    filename: str
    mime_type: str
    size_bytes: int
    content: bytes | None = None        # сырьё для Document Service
    extracted_text: str | None = None   # результат извлечения (узел 4)
    ocr_used: bool = False


class EmailMessage(BaseModel):
    """Распарсенное входящее письмо (узел 1)."""

    message_id: str
    mailbox: str
    sender_email: str
    sender_name: str | None = None
    subject: str = ""
    body_text: str = ""
    body_html: str | None = None
    received_at: datetime
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    routing_recipient: str | None = None
    reply_to: str | None = None
    list_unsubscribe: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)


class SpamResult(BaseModel):
    is_spam: bool
    confidence: float          # 0.0–1.0
    reason: str
    rule_hit: str | None = None  # какое правило сработало (этап 2.1)


class Contractor(BaseModel):
    """Запись RAG-коллекции contractors."""

    contractor_id: str
    name: str
    emails: list[str]
    department_codes: list[str]   # допустимые отделы
    contractor_type: str          # клиент / поставщик / партнёр / госорган


class SenderIdentity(BaseModel):
    found: bool
    contractor: Contractor | None = None
    is_new_contractor: bool = False
    allowed_departments: list[str] = Field(default_factory=list)


class Department(BaseModel):
    """Запись RAG-коллекции departments."""

    department_id: str
    department_name: str
    head_name: str                # ФИО руководителя — для задачи в 1С
    responsibility: str = ""
    keywords: list[str] = Field(default_factory=list)


class DepartmentRecord(BaseModel):
    """Запись справочника departments в PostgreSQL."""

    code: str
    name: str
    direction: str | None = None
    email: str | None = None
    is_active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class RoutingResult(BaseModel):
    department_id: str
    department_name: str
    confidence: float
    reasoning: str
    priority: Priority = Priority.NORMAL
    document_kind: str | None = None
    queue_tier: int = 1
    register_erp: bool = True


class ErpTaskResult(BaseModel):
    success: bool
    erp_document_number: str | None = None
    erp_task_id: str | None = None
    error: str | None = None
