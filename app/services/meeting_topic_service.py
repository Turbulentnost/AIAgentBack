from __future__ import annotations

import asyncio
from typing import Any

from app.schemas.meeting_topic import (
    MeetingTopicCheckSimilarRead,
    MeetingTopicCheckSimilarRequest,
    MeetingTopicParticipantRead,
    MeetingTopicResolveRead,
    MeetingTopicResolveRequest,
    MeetingTopicSimilarityBreakdownRead,
    MeetingTopicSummaryRead,
    MeetingTopicValidationRead,
)
from app.services.meeting_exceptions import MeetingServiceError
from app.tools.onec.connection import CONFIG, create_session
from app.tools.onec.create_meeting_topic import (
    create_meeting_topic,
    merge_topic_participant_fios,
    normalize_participant_fios,
    require_topic_participant_fios,
)
from app.tools.onec.lookup_user_ref import resolve_user_by_fio
from app.tools.onec.meeting_topic_participants import get_meeting_topic_participants
from app.tools.onec.meeting_topic_similarity import find_similar_topic_for_manager_async
from app.tools.onec.meeting_topics_registry import (
    fetch_topic_by_key,
    normalize_topic,
    topic_closed_date_from_meeting_start,
    update_meeting_topic_closed_date,
)

CREATE_TOPIC_REQUIRED_FIELDS = [
    "description",
    "manager_fio",
    "meeting_type",
    "participant_fios",
]


class MeetingTopicServiceError(MeetingServiceError):
    pass


def is_newly_created_meeting_topic(topic: dict[str, Any] | None) -> bool:
    if not topic:
        return False
    return bool(topic.get("created")) and not bool(topic.get("used_existing"))


async def sync_new_topic_closed_date_after_scheduling(
    topic: dict[str, Any],
    slot_start: str,
) -> dict[str, Any] | None:
    """Для новой темы при планировании совещания ставит дату закрытия = дата совещания + 2 недели."""
    ref_key = str(topic.get("ref_key") or "").strip()
    if not ref_key or not is_newly_created_meeting_topic(topic):
        return None

    closed_date = topic_closed_date_from_meeting_start(slot_start)

    def _update() -> dict[str, Any]:
        return update_meeting_topic_closed_date(ref_key, closed_date)

    await asyncio.to_thread(_update)
    return {"ref_key": ref_key, "closed_date": closed_date}


def _breakdown_from_topic(topic: dict[str, Any]) -> MeetingTopicSimilarityBreakdownRead | None:
    raw = topic.get("similarity_breakdown")
    if not isinstance(raw, dict):
        return None
    return MeetingTopicSimilarityBreakdownRead.model_validate(raw)


def _participants_from_raw(raw: dict[str, Any]) -> list[MeetingTopicParticipantRead]:
    participants = raw.get("participants") or []
    return [
        MeetingTopicParticipantRead(
            participant_ref_key=item.get("participant_ref_key"),
            fio=item.get("fio"),
        )
        for item in participants
        if isinstance(item, dict)
    ]


def _summary_from_topic(
    topic: dict[str, Any],
    *,
    participants: list[MeetingTopicParticipantRead] | None = None,
) -> MeetingTopicSummaryRead:
    return MeetingTopicSummaryRead(
        ref_key=topic.get("ref_key"),
        code=topic.get("code"),
        description=topic.get("description") or "",
        details=topic.get("details"),
        meeting_type=topic.get("meeting_type"),
        manager=topic.get("manager"),
        reviewer=topic.get("reviewer"),
        department=topic.get("department"),
        room=topic.get("room"),
        project=topic.get("project"),
        committee=topic.get("committee"),
        start_time=topic.get("start_time"),
        end_time=topic.get("end_time"),
        closed_date=topic.get("closed_date"),
        is_active=bool(topic.get("is_active", True)),
        similarity_score=topic.get("similarity_score"),
        similarity_method=topic.get("similarity_method"),
        similarity_breakdown=_breakdown_from_topic(topic),
        participants=participants or [],
    )


async def _load_topic_participants(topic_ref_key: str) -> list[MeetingTopicParticipantRead]:
    raw = await asyncio.to_thread(
        get_meeting_topic_participants,
        topic_ref_key=topic_ref_key,
    )
    return _participants_from_raw(raw)


async def _load_topic_detail(topic_ref_key: str) -> MeetingTopicSummaryRead:
    def _fetch() -> dict[str, Any]:
        session = create_session(CONFIG)
        topic = fetch_topic_by_key(
            session,
            CONFIG,
            topic_ref_key,
            expand_related=True,
        )
        if not topic:
            raise MeetingTopicServiceError(
                f"Тема совещания не найдена: {topic_ref_key}",
                status_code=404,
            )
        # OData by Ref_Key не поддерживает $expand — fetch_topic_by_key откатывается без expand.
        return normalize_topic(topic, expand_related=False)

    topic = await asyncio.to_thread(_fetch)
    participants = await _load_topic_participants(topic_ref_key)
    return _summary_from_topic(topic, participants=participants)


class MeetingTopicService:
    async def check_similar(
        self,
        payload: MeetingTopicCheckSimilarRequest,
    ) -> MeetingTopicCheckSimilarRead:
        def _resolve_manager() -> tuple[Any, str]:
            session = create_session(CONFIG)
            manager_ref, resolved_manager_fio, _ = resolve_user_by_fio(
                session,
                payload.manager_fio,
                config=CONFIG,
            )
            return session, manager_ref, resolved_manager_fio

        session, manager_ref, _resolved_manager_fio = await asyncio.to_thread(_resolve_manager)

        similar_topic = await find_similar_topic_for_manager_async(
            session,
            CONFIG,
            manager_ref_key=manager_ref,
            description=payload.description,
            meeting_type=payload.meeting_type,
            topic_details=payload.topic_details,
            participant_fios=merge_topic_participant_fios(
                payload.participant_fios,
                manager_fio=payload.manager_fio,
                initiator_fio=payload.initiator_fio,
            ),
        )

        if not similar_topic:
            return MeetingTopicCheckSimilarRead(
                similar_found=False,
                requires_user_decision=True,
                required_fields=list(CREATE_TOPIC_REQUIRED_FIELDS),
                message=(
                    "Похожая активная тема у руководителя не найдена. "
                    "Заполните поля для создания новой темы."
                ),
            )

        ref_key = str(similar_topic.get("ref_key") or "")
        participants = await _load_topic_participants(ref_key) if ref_key else []
        summary = _summary_from_topic(similar_topic, participants=participants)
        code = summary.code or "?"
        return MeetingTopicCheckSimilarRead(
            similar_found=True,
            requires_user_decision=True,
            similar_topic=summary,
            similarity_score=summary.similarity_score,
            similarity_method=summary.similarity_method,
            similarity_breakdown=summary.similarity_breakdown,
            required_fields=list(CREATE_TOPIC_REQUIRED_FIELDS),
            message=(
                f"У руководителя уже есть похожая тема №{code}: {summary.description}"
                f"{f' ({summary.meeting_type})' if summary.meeting_type else ''}. "
                "Использовать её или создать новую? При использовании существующей темы "
                "совещание оформляется с тем же названием и видом совещания из 1С."
            ),
        )

    async def resolve(
        self,
        payload: MeetingTopicResolveRequest,
    ) -> MeetingTopicResolveRead:
        if payload.decision == "use_existing":
            ref_key = str(payload.existing_topic_ref_key or "").strip()
            topic = await _load_topic_detail(ref_key)
            if not topic.participants:
                raise MeetingTopicServiceError(
                    "У выбранной темы совещания не указаны участники. "
                    "Создайте новую тему с участниками или дополните тему в 1С.",
                    status_code=400,
                )
            return MeetingTopicResolveRead(
                decision="use_existing",
                used_existing=True,
                created=False,
                dry_run=False,
                topic=topic,
                participants_count=len(topic.participants),
                message=(
                    f"Используется существующая тема №{topic.code or '?'}: "
                    f"{topic.description}"
                    f"{f' ({topic.meeting_type})' if topic.meeting_type else ''}. "
                    "Совещание оформляется с тем же названием и видом совещания из 1С."
                ),
            )

        raw = await asyncio.to_thread(
            create_meeting_topic,
            description=str(payload.description),
            manager_fio=str(payload.manager_fio),
            meeting_type=str(payload.meeting_type or "Отчетное"),
            reviewer_fio=payload.reviewer_fio,
            closed_date=payload.closed_date,
            closed_end_of_year=payload.closed_end_of_year,
            department_key=payload.department_key,
            room_key=payload.room_key,
            project_key=payload.project_key,
            committee_key=payload.committee_key,
            organization_key=payload.organization_key,
            start_time=payload.start_time,
            end_time=payload.end_time,
            is_management_circle_topic=payload.is_management_circle_topic,
            topic_details=payload.topic_details,
            participant_fios=require_topic_participant_fios(
                merge_topic_participant_fios(
                    payload.participant_fios,
                    manager_fio=str(payload.manager_fio),
                    initiator_fio=payload.initiator_fio,
                )
            ),
            initiator_fio=payload.initiator_fio,
            skip_similarity_check=True,
            dry_run=payload.dry_run,
        )
        topic_raw = raw.get("topic") or {}
        participants = _participants_from_raw(raw)
        topic = _summary_from_topic(topic_raw, participants=participants)
        return MeetingTopicResolveRead(
            decision="create_new",
            used_existing=False,
            created=bool(raw.get("created")),
            dry_run=bool(raw.get("dry_run")),
            topic=topic,
            participants_count=int(raw.get("participants_count") or len(participants)),
            message=str(
                raw.get("message")
                or f"Создана тема совещания №{topic.code or '?'}."
            ),
        )

    async def validate_topic_ref_key(self, topic_ref_key: str) -> MeetingTopicValidationRead:
        normalized_ref = str(topic_ref_key or "").strip()
        if not normalized_ref:
            return MeetingTopicValidationRead(
                valid=False,
                reason="Не указан Ref_Key темы совещания",
            )

        def _fetch() -> dict[str, Any] | None:
            session = create_session(CONFIG)
            return fetch_topic_by_key(
                session,
                CONFIG,
                normalized_ref,
                expand_related=False,
            )

        row = await asyncio.to_thread(_fetch)
        if not row:
            return MeetingTopicValidationRead(
                valid=False,
                reason="Тема совещания не найдена в 1С или удалена",
            )

        topic = normalize_topic(row, expand_related=False)
        participants = await _load_topic_participants(normalized_ref)
        summary = _summary_from_topic(topic, participants=participants)
        if not summary.participants:
            return MeetingTopicValidationRead(
                valid=False,
                topic=summary,
                reason="У темы совещания не указаны участники",
            )
        return MeetingTopicValidationRead(valid=True, topic=summary)
