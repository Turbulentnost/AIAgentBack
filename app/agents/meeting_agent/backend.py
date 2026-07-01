from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.meeting_invite_format import (
    format_invite_location,
    invite_body_from_attendees,
    manager_name_from_memo_document,
    place_from_invite_location,
    place_from_memo_document,
)
from app.agents.meeting_agent.memo_presenter import resolve_meeting_schedule
from app.core.config import settings
from app.models.user import User
from app.tools.executor import ToolExecutor, ToolExecutionError
from app.tools.schemas import ToolContext

DEFAULT_DURATION_MINUTES = 60
SLOT_PREVIEW_MAX_DAYS = 30
SLOT_PREVIEW_TIMEOUT_SECONDS = 180
MEMO_FETCH_LIMIT = 50
MEMO_FETCH_POOL = 200


class MeetingBackendError(ValueError):
    pass


@dataclass(slots=True)
class MeetingMemo:
    ref_key: str | None
    number: str | None
    date: str | None
    subject: str | None
    meeting_type: str | None
    participant_fio: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedParticipant:
    fio: str
    email: str | None
    found: bool


@dataclass(slots=True)
class MeetingSlot:
    start: str
    end: str
    confidence: float


@dataclass(slots=True)
class MeetingRoomOption:
    name: str
    email: str | None
    available: bool | None


@dataclass(slots=True)
class InviteDraft:
    subject: str
    start: str
    end: str
    location: str
    attendees: list[str]
    body: str


class MeetingBackend:
    """Оркестрация инструментов 1С/Outlook для узлов графа meeting_agent."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        tool_executor: ToolExecutor | None = None,
        agent_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
    ) -> None:
        self.db = db
        self.tool_executor = tool_executor or ToolExecutor()
        self.agent_id = agent_id
        self.task_id = task_id

    async def load_memo(
        self,
        *,
        memo_ref_key: str | None = None,
        memo_number: str | None = None,
        current_user: User,
    ) -> MeetingMemo:
        if memo_ref_key and settings.MEETING_DASHBOARD_CACHE_ENABLED:
            from app.services.meeting_memo_cache import (
                MeetingMemoCacheService,
                MemoCacheMissError,
                detail_to_memo_document,
            )

            try:
                detail, _, _ = await MeetingMemoCacheService().get_memo_detail(memo_ref_key)
                return _normalize_memo(detail_to_memo_document(detail))
            except MemoCacheMissError as exc:
                raise MeetingBackendError(str(exc)) from exc

        payload = await self._invoke(
            "get_meeting_memos",
            {"limit": MEMO_FETCH_LIMIT, "fetch_pool": MEMO_FETCH_POOL, "compact": False},
            current_user=current_user,
        )
        document = _find_memo_document(payload.get("documents") or [], memo_ref_key, memo_number)
        if document is None:
            raise MeetingBackendError("Служебная записка не найдена в 1С")
        return _normalize_memo(document)

    async def validate_memo(
        self,
        memo: MeetingMemo | dict[str, Any] | None,
        *,
        current_user: User,
    ) -> list[MemoValidationIssue]:
        del current_user
        document = memo.raw if isinstance(memo, MeetingMemo) else memo
        return validate_meeting_memo_document(document)

    async def resolve_participants(
        self,
        fio_list: list[str],
        *,
        current_user: User,
    ) -> list[ResolvedParticipant]:
        normalized = [item.strip() for item in fio_list if item and item.strip()]
        if not normalized:
            return []

        try:
            payload = await self._invoke("lookup_email_by_fio", {"fio": normalized}, current_user=current_user)
        except ToolExecutionError as exc:
            raise MeetingBackendError(f"Не удалось найти e-mail участников: {exc}") from exc

        by_query = {item["fio_query"]: item for item in payload.get("results") or []}
        resolved: list[ResolvedParticipant] = []
        for fio in normalized:
            match = by_query.get(fio)
            email = _pick_corporate_email(match.get("emails") if match else None)
            resolved.append(
                ResolvedParticipant(
                    fio=fio,
                    email=email,
                    found=bool(email),
                )
            )
        return resolved

    async def find_slots(
        self,
        *,
        memo: MeetingMemo | dict[str, Any] | None,
        participants: list[ResolvedParticipant | dict[str, Any]],
        planned_start: str | None,
        duration_minutes: int | None,
        current_user: User,
        max_days: int = 30,
        verify_calendar: bool = True,
        quiet: bool = True,
        include_timing: bool = False,
    ) -> list[MeetingSlot]:
        attendee_emails = _participant_emails(participants)
        if not attendee_emails:
            return []

        duration = duration_minutes or _duration_from_memo(memo) or DEFAULT_DURATION_MINUTES
        preferred = planned_start or _preferred_from_memo(memo) or _default_preferred()

        try:
            payload = await self._invoke(
                "find_meeting_slot",
                {
                    "attendees": attendee_emails,
                    "preferred": preferred,
                    "duration_minutes": duration,
                    "max_days": max_days,
                    "verify_calendar": verify_calendar,
                    "skip_rooms": True,
                    "quiet": quiet,
                    "include_timing": include_timing,
                },
                current_user=current_user,
            )
        except ToolExecutionError as exc:
            raise MeetingBackendError(f"Не удалось подобрать время совещания: {exc}") from exc
        except Exception as exc:
            raise MeetingBackendError(str(exc)) from exc

        slot_start = payload.get("slot_start")
        slot_end = payload.get("slot_end")
        if not slot_start or not slot_end:
            return []

        confidence = 0.95 if len(attendee_emails) == len([p for p in participants if _participant_found(p)]) else 0.7
        return [MeetingSlot(start=slot_start, end=slot_end, confidence=confidence)]

    async def find_rooms(
        self,
        *,
        selected_slot: dict[str, Any] | MeetingSlot | None,
        room_name: str | None,
        current_user: User,
    ) -> list[MeetingRoomOption]:
        slot = _slot_dict(selected_slot)
        if slot and slot.get("start"):
            duration = _slot_duration_minutes(slot) or DEFAULT_DURATION_MINUTES
            params = {
                "list_only": False,
                "check": True,
                "discover": False,
                "start": slot["start"],
                "duration_minutes": duration,
            }
        else:
            params = {"list_only": True, "check": False, "discover": False}

        try:
            payload = await self._invoke("meeting_rooms", params, current_user=current_user)
        except ToolExecutionError as exc:
            raise MeetingBackendError(f"Не удалось получить переговорные: {exc}") from exc

        rooms = _rooms_from_payload(payload, room_name=room_name)
        if room_name and not rooms:
            rooms = [
                MeetingRoomOption(name=room_name, email=None, available=None),
            ]
        return rooms

    async def prepare_invite(
        self,
        *,
        memo: MeetingMemo | dict[str, Any] | None,
        participants: list[ResolvedParticipant | dict[str, Any]],
        selected_slot: dict[str, Any] | MeetingSlot | None,
        selected_room: dict[str, Any] | MeetingRoomOption | None,
        subject: str | None,
        current_user: User,
    ) -> InviteDraft | None:
        del current_user
        slot = _slot_dict(selected_slot)
        if not slot or not slot.get("start") or not slot.get("end"):
            return None

        memo_obj = memo if isinstance(memo, MeetingMemo) else _normalize_memo(memo or {})
        room = _room_dict(selected_room)
        attendees = _participant_emails(participants)
        if not attendees:
            return None

        invite_subject = subject or memo_obj.subject or f"Совещание {memo_obj.number or ''}".strip()
        manager_name = manager_name_from_memo_document(memo_obj.raw)
        place = room.get("name") or place_from_memo_document(memo_obj.raw) or ""
        location = format_invite_location(manager_name, place)
        body = invite_body_from_attendees(participants)

        return InviteDraft(
            subject=invite_subject,
            start=slot["start"],
            end=slot["end"],
            location=location,
            attendees=attendees,
            body=body,
        )

    async def send_invite(
        self,
        invite: InviteDraft | dict[str, Any],
        *,
        current_user: User,
    ) -> dict[str, Any]:
        draft = invite if isinstance(invite, InviteDraft) else InviteDraft(**invite)
        if not draft.attendees:
            raise MeetingBackendError("Нет участников для отправки приглашения")

        attendee, *extra = draft.attendees
        duration = _slot_duration_minutes({"start": draft.start, "end": draft.end}) or DEFAULT_DURATION_MINUTES
        resources = []
        room_name = place_from_invite_location(draft.location) or draft.location
        if room_name:
            room = await self.find_rooms(
                selected_slot={"start": draft.start, "end": draft.end},
                room_name=room_name,
                current_user=current_user,
            )
            if room and room[0].email:
                resources = [room[0].email]

        return await self._invoke(
            "send_meeting_invite",
            {
                "attendee": attendee,
                "attendees": extra,
                "subject": draft.subject,
                "start": draft.start,
                "duration_minutes": duration,
                "body": draft.body,
                "location": draft.location,
                "resources": resources,
            },
            current_user=current_user,
        )

    async def _invoke(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        current_user: User,
    ) -> dict[str, Any]:
        context = ToolContext(
            db=self.db,
            user=current_user,
            agent_id=self.agent_id,
            task_id=self.task_id,
        )
        result = await self.tool_executor.invoke(
            tool_name=tool_name,
            params=params,
            context=context,
            allowed_tools=_MEETING_TOOL_NAMES,
        )
        if not isinstance(result, dict):
            raise MeetingBackendError(f"Инструмент {tool_name} вернул неожиданный ответ")
        return result

    async def run_agent(self, payload: dict, *, current_user: User):
        from app.agents.meeting_agent.service import MeetingAgent

        agent = MeetingAgent()
        return await agent.run({**payload, "backend": self, "current_user": current_user})


_MEETING_TOOL_NAMES = [
    "get_meeting_dashboard",
    "get_meeting_memos",
    "get_meeting_topics_registry",
    "lookup_email_by_fio",
    "find_meeting_slot",
    "meeting_rooms",
    "send_meeting_invite",
    "reschedule_meeting",
    "cancel_meeting",
    "create_service_memo",
    "approve_service_memo",
    "reject_service_memo",
    "send_desktop_notification",
]


def _find_memo_document(
    documents: list[dict[str, Any]],
    memo_ref_key: str | None,
    memo_number: str | None,
) -> dict[str, Any] | None:
    ref_key = (memo_ref_key or "").strip().lower()
    number = (memo_number or "").strip().lower()
    for document in documents:
        memo = document.get("memo") or {}
        doc_ref = str(memo.get("Ref_Key") or "").lower()
        doc_number = str(memo.get("Number") or "").lower()
        if ref_key and doc_ref == ref_key:
            return document
        if number and doc_number == number:
            return document
    return None


def _normalize_memo(document: dict[str, Any] | None) -> MeetingMemo:
    if not document:
        return MeetingMemo(
            ref_key=None,
            number=None,
            date=None,
            subject=None,
            meeting_type=None,
            participant_fio=[],
            raw={},
        )

    memo = document.get("memo") or {}
    subject = _memo_subject(memo, document)
    meeting_type = _memo_meeting_type(memo, document)
    return MeetingMemo(
        ref_key=memo.get("Ref_Key"),
        number=memo.get("Number"),
        date=memo.get("Date"),
        subject=subject,
        meeting_type=meeting_type,
        participant_fio=_extract_participant_fio(document),
        raw=document,
    )


def _memo_subject(memo: dict[str, Any], document: dict[str, Any]) -> str | None:
    for key in ("Комментарий", "Subject", "Description", "Тема"):
        value = memo.get(key) or (document.get("header") or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _memo_meeting_type(memo: dict[str, Any], document: dict[str, Any]) -> str | None:
    header = document.get("header") or {}
    for key in ("ВидСовещания", "MeetingType", "ТипСовещания"):
        value = memo.get(key) or header.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_participant_fio(document: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for participant in document.get("participants") or []:
        if not isinstance(participant, dict):
            continue
        for key in ("Description", "ФИО", "Participant", "Участник"):
            value = participant.get(key)
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
                break
    if names:
        return list(dict.fromkeys(names))

    sections = document.get("tabular_sections") or {}
    for rows in sections.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key, value in row.items():
                if ("Участник" in key or "ФИО" in key) and isinstance(value, str) and value.strip():
                    names.append(value.strip())
    return list(dict.fromkeys(names))


def _pick_corporate_email(emails: list[dict[str, Any]] | None) -> str | None:
    if not emails:
        return None
    domain = (settings.ONEC_CORPORATE_EMAIL_DOMAIN or "turbo-don.ru").lower()
    for item in emails:
        address = str(item.get("email") or item.get("address") or "").strip()
        if address and address.lower().endswith(f"@{domain}"):
            return address
    first = emails[0]
    return str(first.get("email") or first.get("address") or "").strip() or None


def _participant_emails(participants: list[ResolvedParticipant | dict[str, Any]]) -> list[str]:
    emails: list[str] = []
    for participant in participants:
        email = participant.email if isinstance(participant, ResolvedParticipant) else participant.get("email")
        if email:
            emails.append(str(email))
    return list(dict.fromkeys(emails))


def _participant_found(participant: ResolvedParticipant | dict[str, Any]) -> bool:
    if isinstance(participant, ResolvedParticipant):
        return participant.found
    return bool(participant.get("found"))


def _duration_from_memo(memo: MeetingMemo | dict[str, Any] | None) -> int | None:
    document = memo.raw if isinstance(memo, MeetingMemo) else memo
    if not document:
        return None
    header = document.get("header") or {}
    memo_fields = document.get("memo") or {}
    for source in (header, memo_fields):
        for key in ("Длительность", "DurationMinutes", "Продолжительность"):
            value = source.get(key)
            if isinstance(value, int) and value > 0:
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
    return None


def _preferred_from_memo(memo: MeetingMemo | dict[str, Any] | None) -> str | None:
    document = memo.raw if isinstance(memo, MeetingMemo) else memo
    if not document:
        return None
    header = document.get("header") or document.get("memo") or {}
    start, _end = resolve_meeting_schedule(header)
    if start is not None:
        return start.strftime("%Y-%m-%d %H:%M")

    memo_fields = document.get("memo") or {}
    for source in (header, memo_fields):
        for key in ("ДатаСовещания", "PlannedStart", "НачалоСовещания", "ВремяНачалаСовещания", "Date"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return _normalize_datetime_string(value.strip())
    return None


def _organizer_email() -> str | None:
    mailbox = (settings.OUTLOOK_MAILBOX or settings.OUTLOOK_EMAIL or "").strip()
    return mailbox or None


def _participant_emails_with_organizer(emails: list[str]) -> list[str]:
    organizer = _organizer_email()
    if not organizer:
        return emails
    normalized = {email.lower(): email for email in emails}
    if organizer.lower() not in normalized:
        return [*emails, organizer]
    return emails


def _default_preferred() -> str:
    tz = ZoneInfo(settings.OUTLOOK_TIMEZONE or "Europe/Moscow")
    now = datetime.now(tz)
    candidate = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.strftime("%Y-%m-%d %H:%M")


def _normalize_datetime_string(value: str) -> str:
    cleaned = value.replace("T", " ")
    if len(cleaned) >= 16:
        return cleaned[:16]
    return cleaned


def _slot_dict(slot: dict[str, Any] | MeetingSlot | None) -> dict[str, Any]:
    if slot is None:
        return {}
    if isinstance(slot, MeetingSlot):
        return {"start": slot.start, "end": slot.end, "confidence": slot.confidence}
    return dict(slot)


def _room_dict(room: dict[str, Any] | MeetingRoomOption | None) -> dict[str, Any]:
    if room is None:
        return {}
    if isinstance(room, MeetingRoomOption):
        return {"name": room.name, "email": room.email, "available": room.available}
    return dict(room)


def _slot_duration_minutes(slot: dict[str, Any]) -> int | None:
    start = slot.get("start")
    end = slot.get("end")
    if not start or not end:
        return None
    try:
        start_dt = datetime.fromisoformat(str(start).replace(" ", "T"))
        end_dt = datetime.fromisoformat(str(end).replace(" ", "T"))
    except ValueError:
        return None
    minutes = int((end_dt - start_dt).total_seconds() // 60)
    return minutes if minutes > 0 else None


def _rooms_from_payload(payload: dict[str, Any], *, room_name: str | None) -> list[MeetingRoomOption]:
    normalized_name = (room_name or "").strip().lower()
    rooms_status = payload.get("rooms_status")
    if isinstance(rooms_status, list) and rooms_status:
        options = []
        for item in rooms_status:
            name = str(item.get("name") or item.get("room") or "").strip()
            if normalized_name and normalized_name not in name.lower():
                continue
            options.append(
                MeetingRoomOption(
                    name=name,
                    email=item.get("email"),
                    available=item.get("status") == "free",
                )
            )
        free = [item for item in options if item.available]
        return free or options

    options = []
    for item in payload.get("rooms") or []:
        name = str(item.get("name") or item.get("title") or "").strip()
        if normalized_name and normalized_name not in name.lower():
            continue
        options.append(
            MeetingRoomOption(
                name=name,
                email=item.get("email"),
                available=None,
            )
        )
    return options


def _invite_body(memo: MeetingMemo) -> str:
    del memo
    return invite_body_from_attendees([])
