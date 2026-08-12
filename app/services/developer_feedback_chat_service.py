from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.developer_feedback import (
    DeveloperFeedbackAttachment,
    DeveloperFeedbackMessage,
    DeveloperFeedbackThread,
)
from app.models.user import User
from app.services.document_analysis_permission import (
    AVION_ONLY_PLATFORM_USERS,
    DOCUMENT_ANALYSIS_AGENT_SLUG,
)


DEVELOPER_FEEDBACK_ADMIN_EMAIL = "sktb_razvitie5@turbo-don.ru"
DEVELOPER_FEEDBACK_ALLOWED_EMAILS = frozenset(spec.email for spec in AVION_ONLY_PLATFORM_USERS)
DEVELOPER_FEEDBACK_ALLOWED_NAMES = frozenset(spec.full_name for spec in AVION_ONLY_PLATFORM_USERS)
DEVELOPER_FEEDBACK_STORAGE_DIR = (
    Path(__file__).resolve().parents[2] / "data" / "aveon" / "feedback"
)
MAX_FEEDBACK_ATTACHMENT_SIZE = 15 * 1024 * 1024
MAX_FEEDBACK_ATTACHMENTS_PER_MESSAGE = 8
_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9а-яА-ЯёЁ._ -]+")


class DeveloperFeedbackAccessError(PermissionError):
    pass


class DeveloperFeedbackValidationError(ValueError):
    pass


@dataclass(frozen=True)
class DeveloperFeedbackUpload:
    filename: str
    content: bytes
    content_type: str | None = None


def normalize_feedback_email(email: str | None) -> str:
    return (email or "").strip().casefold()


def is_developer_feedback_admin(user: User | None) -> bool:
    return normalize_feedback_email(user.email if user else None) == DEVELOPER_FEEDBACK_ADMIN_EMAIL


def is_developer_feedback_participant(user: User | None) -> bool:
    if user is None:
        return False
    email = normalize_feedback_email(user.email)
    full_name = (user.full_name or "").strip()
    return email in DEVELOPER_FEEDBACK_ALLOWED_EMAILS or full_name in DEVELOPER_FEEDBACK_ALLOWED_NAMES


def feedback_user_display_name(user: User | None) -> str:
    if user is None:
        return "Неизвестный пользователь"
    if is_developer_feedback_admin(user):
        return "Разработчик Авион"
    parts = [user.last_name, user.first_name, user.middle_name]
    full_name = " ".join(part.strip() for part in parts if part and part.strip())
    return full_name or (user.full_name or "").strip() or user.email or "Неизвестный пользователь"


def _safe_filename(filename: str) -> str:
    cleaned = _SAFE_FILENAME_RE.sub("_", (filename or "attachment").strip())
    cleaned = cleaned.strip(" ._") or "attachment"
    return cleaned[:180]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # TimestampMixin stores naive timestamps in the server local timezone.
        local_tz = datetime.now().astimezone().tzinfo
        return value.replace(tzinfo=local_tz).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def _read_marker(now: datetime, thread: DeveloperFeedbackThread) -> datetime:
    marker = _normalize_utc(now) or now
    last_at = _normalize_utc(thread.last_message_at)
    if last_at is not None and last_at > marker:
        return last_at
    return marker


class DeveloperFeedbackChatService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def user_mode(self, user: User) -> str:
        return "developer" if is_developer_feedback_admin(user) else "user"

    async def list_threads(self, user: User) -> list[DeveloperFeedbackThread]:
        if is_developer_feedback_admin(user):
            await self.ensure_participant_threads()
            result = await self.db.scalars(
                select(DeveloperFeedbackThread)
                .options(selectinload(DeveloperFeedbackThread.messages))
                .where(DeveloperFeedbackThread.agent_slug == DOCUMENT_ANALYSIS_AGENT_SLUG)
                .order_by(
                    DeveloperFeedbackThread.last_message_at.desc().nullslast(),
                    DeveloperFeedbackThread.participant_name.asc(),
                )
            )
            return [
                thread
                for thread in result.unique().all()
                if normalize_feedback_email(thread.participant_email) in DEVELOPER_FEEDBACK_ALLOWED_EMAILS
                or thread.participant_name in DEVELOPER_FEEDBACK_ALLOWED_NAMES
            ]

        if not is_developer_feedback_participant(user):
            raise DeveloperFeedbackAccessError("Обратная связь доступна только пользователям Авион.")
        thread = await self.ensure_user_thread(user)
        loaded = await self.get_thread_for_user(user, thread.id)
        return [loaded]

    async def ensure_participant_threads(self) -> list[DeveloperFeedbackThread]:
        users = await self._load_allowed_users()
        threads = []
        for user in users:
            threads.append(await self.ensure_user_thread(user))
        return threads

    async def ensure_user_thread(self, user: User) -> DeveloperFeedbackThread:
        thread = await self.db.scalar(
            select(DeveloperFeedbackThread).where(
                DeveloperFeedbackThread.agent_slug == DOCUMENT_ANALYSIS_AGENT_SLUG,
                DeveloperFeedbackThread.participant_user_id == user.id,
            )
        )
        name = feedback_user_display_name(user)
        email = normalize_feedback_email(user.email)
        if thread is None:
            now = _utc_now()
            thread = DeveloperFeedbackThread(
                agent_slug=DOCUMENT_ANALYSIS_AGENT_SLUG,
                participant_user_id=user.id,
                participant_name=name,
                participant_email=email,
                status="open",
                participant_last_read_at=now,
                developer_last_read_at=now,
            )
            self.db.add(thread)
            await self.db.flush()
        else:
            thread.participant_name = name
            thread.participant_email = email
        return thread

    async def get_thread_for_user(self, user: User, thread_id: uuid.UUID) -> DeveloperFeedbackThread:
        thread = await self.db.scalar(
            select(DeveloperFeedbackThread)
            .options(
                selectinload(DeveloperFeedbackThread.messages).selectinload(
                    DeveloperFeedbackMessage.attachments
                )
            )
            .where(DeveloperFeedbackThread.id == thread_id)
        )
        if thread is None:
            raise DeveloperFeedbackValidationError("Диалог обратной связи не найден.")
        self._ensure_thread_access(user, thread)
        return thread

    async def add_message(
        self,
        user: User,
        *,
        body: str,
        thread_id: uuid.UUID | None = None,
        attachments: list[DeveloperFeedbackUpload] | None = None,
    ) -> tuple[DeveloperFeedbackThread, DeveloperFeedbackMessage]:
        body = (body or "").strip()
        if len(body) < 1:
            raise DeveloperFeedbackValidationError("Сообщение не может быть пустым.")

        uploads = attachments or []
        if len(uploads) > MAX_FEEDBACK_ATTACHMENTS_PER_MESSAGE:
            raise DeveloperFeedbackValidationError(
                f"Можно прикрепить не больше {MAX_FEEDBACK_ATTACHMENTS_PER_MESSAGE} файлов."
            )

        if is_developer_feedback_admin(user):
            if thread_id is None:
                raise DeveloperFeedbackValidationError("Для ответа разработчика нужен диалог.")
            thread = await self.get_thread_for_user(user, thread_id)
            author_role = "developer"
        else:
            if not is_developer_feedback_participant(user):
                raise DeveloperFeedbackAccessError("Обратная связь доступна только пользователям Авион.")
            thread = await self.ensure_user_thread(user)
            if thread_id is not None and thread.id != thread_id:
                raise DeveloperFeedbackAccessError("Нельзя писать в чужой диалог.")
            author_role = "user"

        now = _utc_now()
        message = DeveloperFeedbackMessage(
            thread_id=thread.id,
            author_user_id=user.id,
            author_role=author_role,
            author_name=feedback_user_display_name(user),
            author_email=normalize_feedback_email(user.email),
            body=body,
        )
        self.db.add(message)
        await self.db.flush()

        for upload in uploads:
            attachment = self._store_attachment(message.id, upload)
            self.db.add(attachment)

        thread.last_message_at = now
        thread.status = "open"
        if author_role == "developer":
            thread.developer_last_read_at = now
        else:
            thread.participant_last_read_at = now
        await self.db.flush()
        loaded_message = await self.db.scalar(
            select(DeveloperFeedbackMessage)
            .options(selectinload(DeveloperFeedbackMessage.attachments))
            .where(DeveloperFeedbackMessage.id == message.id)
        )
        if loaded_message is None:
            raise DeveloperFeedbackValidationError("Сообщение не найдено.")
        loaded_thread = await self.db.scalar(
            select(DeveloperFeedbackThread)
            .options(selectinload(DeveloperFeedbackThread.messages))
            .where(DeveloperFeedbackThread.id == thread.id)
        )
        if loaded_thread is None:
            raise DeveloperFeedbackValidationError("Диалог обратной связи не найден.")
        return loaded_thread, loaded_message

    async def mark_thread_read(self, user: User, thread_id: uuid.UUID) -> DeveloperFeedbackThread:
        thread = await self.get_thread_for_user(user, thread_id)
        read_at = _read_marker(_utc_now(), thread)
        if is_developer_feedback_admin(user):
            thread.developer_last_read_at = read_at
        else:
            thread.participant_last_read_at = read_at
        await self.db.flush()
        return await self.get_thread_for_user(user, thread_id)

    async def get_attachment_for_user(
        self,
        user: User,
        attachment_id: uuid.UUID,
    ) -> DeveloperFeedbackAttachment:
        attachment = await self.db.scalar(
            select(DeveloperFeedbackAttachment)
            .join(DeveloperFeedbackMessage)
            .join(DeveloperFeedbackThread)
            .options(
                selectinload(DeveloperFeedbackAttachment.message).selectinload(
                    DeveloperFeedbackMessage.thread
                )
            )
            .where(DeveloperFeedbackAttachment.id == attachment_id)
        )
        if attachment is None:
            raise DeveloperFeedbackValidationError("Вложение не найдено.")
        self._ensure_thread_access(user, attachment.message.thread)
        return attachment

    def unread_count(self, user: User, thread: DeveloperFeedbackThread) -> int:
        read_at = _normalize_utc(
            thread.developer_last_read_at
            if is_developer_feedback_admin(user)
            else thread.participant_last_read_at
        )
        target_role = "user" if is_developer_feedback_admin(user) else "developer"
        messages = getattr(thread, "messages", []) or []
        return sum(
            1
            for message in messages
            if message.author_role == target_role
            and (
                read_at is None
                or (
                    message.created_at is not None
                    and _normalize_utc(message.created_at) > read_at
                )
            )
        )

    def last_message_preview(self, thread: DeveloperFeedbackThread) -> str | None:
        messages = getattr(thread, "messages", []) or []
        if not messages:
            return None
        text = (messages[-1].body or "").strip().replace("\n", " ")
        return text[:140]

    def attachment_path(self, attachment: DeveloperFeedbackAttachment) -> Path:
        path = (DEVELOPER_FEEDBACK_STORAGE_DIR / attachment.storage_path).resolve()
        storage_root = DEVELOPER_FEEDBACK_STORAGE_DIR.resolve()
        if storage_root not in path.parents and path != storage_root:
            raise DeveloperFeedbackAccessError("Некорректный путь вложения.")
        return path

    async def _load_allowed_users(self) -> list[User]:
        result = await self.db.scalars(
            select(User)
            .where(
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                or_(
                    User.email.in_(DEVELOPER_FEEDBACK_ALLOWED_EMAILS),
                    User.full_name.in_(DEVELOPER_FEEDBACK_ALLOWED_NAMES),
                ),
            )
            .order_by(User.full_name.asc().nullslast(), User.email.asc())
        )
        return list(result.all())

    def _ensure_thread_access(self, user: User, thread: DeveloperFeedbackThread) -> None:
        if is_developer_feedback_admin(user):
            allowed = (
                normalize_feedback_email(thread.participant_email) in DEVELOPER_FEEDBACK_ALLOWED_EMAILS
                or thread.participant_name in DEVELOPER_FEEDBACK_ALLOWED_NAMES
            )
            if allowed:
                return
        elif thread.participant_user_id == user.id and is_developer_feedback_participant(user):
            return
        raise DeveloperFeedbackAccessError("Нет доступа к этому диалогу.")

    def _store_attachment(
        self,
        message_id: uuid.UUID,
        upload: DeveloperFeedbackUpload,
    ) -> DeveloperFeedbackAttachment:
        content = upload.content
        if not content:
            raise DeveloperFeedbackValidationError("Один из прикреплённых файлов пустой.")
        if len(content) > MAX_FEEDBACK_ATTACHMENT_SIZE:
            raise DeveloperFeedbackValidationError(
                f"Файл {upload.filename} превышает лимит {MAX_FEEDBACK_ATTACHMENT_SIZE} байт."
            )

        checksum = hashlib.sha256(content).hexdigest()
        filename = _safe_filename(upload.filename)
        object_name = f"{message_id}/{uuid.uuid4().hex}-{filename}"
        target = DEVELOPER_FEEDBACK_STORAGE_DIR / object_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return DeveloperFeedbackAttachment(
            message_id=message_id,
            original_filename=filename,
            content_type=upload.content_type or "application/octet-stream",
            file_size=len(content),
            checksum=checksum,
            storage_path=object_name,
        )
