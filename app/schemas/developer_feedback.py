from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class DeveloperFeedbackAttachmentRead(ORMModel):
    id: uuid.UUID
    original_filename: str
    content_type: str | None = None
    file_size: int
    checksum: str
    download_url: str
    created_at: datetime


class DeveloperFeedbackMessageRead(ORMModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    author_user_id: uuid.UUID | None = None
    author_role: str
    author_name: str
    author_email: str
    body: str
    created_at: datetime
    attachments: list[DeveloperFeedbackAttachmentRead] = Field(default_factory=list)


class DeveloperFeedbackThreadRead(ORMModel):
    id: uuid.UUID
    participant_user_id: uuid.UUID
    participant_name: str
    participant_email: str
    status: str
    last_message_at: datetime | None = None
    last_message_preview: str | None = None
    unread_count: int = 0
    created_at: datetime
    updated_at: datetime


class DeveloperFeedbackThreadsResponse(BaseModel):
    mode: str
    threads: list[DeveloperFeedbackThreadRead]


class DeveloperFeedbackMessagesResponse(BaseModel):
    mode: str
    thread: DeveloperFeedbackThreadRead
    messages: list[DeveloperFeedbackMessageRead]


class DeveloperFeedbackSendResponse(BaseModel):
    ok: bool = True
    mode: str
    thread: DeveloperFeedbackThreadRead
    message: DeveloperFeedbackMessageRead
