from __future__ import annotations

import uuid
from datetime import datetime

from app.models.enums import NdChangeJournalEventType, NdChangeJournalSource
from app.schemas.common import ORMModel, Page


class NdChangeJournalEntryRead(ORMModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    event_type: NdChangeJournalEventType
    actor_user_id: uuid.UUID | None
    resource_type: str
    resource_id: str
    department_id: uuid.UUID | None
    template_id: uuid.UUID | None
    document_id: uuid.UUID | None
    document_code: str | None
    document_name: str | None
    summary: str
    payload: dict | None
    source: NdChangeJournalSource


NdChangeJournalEntryPage = Page[NdChangeJournalEntryRead]
