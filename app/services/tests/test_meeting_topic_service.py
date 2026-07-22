from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.meeting_topic import (
    MeetingTopicCheckSimilarRequest,
    MeetingTopicParticipantRead,
    MeetingTopicResolveRequest,
    MeetingTopicSummaryRead,
)
from app.services.meeting_topic_service import MeetingTopicService, _load_topic_detail


@pytest.mark.asyncio
async def test_check_similar_returns_existing_topic_card() -> None:
    service = MeetingTopicService()
    similar_topic = {
        "ref_key": "topic-1",
        "code": "000009370",
        "description": "Еженедельное совещание с главным метрологом",
        "details": "Отчёт по метрологии",
        "meeting_type": "Отчетное",
        "similarity_score": 0.91,
        "similarity_method": "embedding",
        "similarity_breakdown": {"topic": 0.95, "participants": 0.87, "details": 0.8},
        "is_active": True,
    }

    with (
        patch(
            "app.services.meeting_topic_service.asyncio.to_thread",
            new=AsyncMock(
                return_value=(
                    object(),
                    "manager-ref",
                    "Мегрелишвили Михаил Эмзарович",
                )
            ),
        ),
        patch(
            "app.services.meeting_topic_service.find_similar_topic_for_manager_async",
            new=AsyncMock(return_value=similar_topic),
        ),
        patch(
            "app.services.meeting_topic_service._load_topic_participants",
            new=AsyncMock(
                return_value=[
                    MeetingTopicParticipantRead(
                        participant_ref_key="p-1",
                        fio="Хозуян Иван Владимирович",
                    )
                ]
            ),
        ),
    ):
        result = await service.check_similar(
            MeetingTopicCheckSimilarRequest(
                description="Еженедельное совещание с главным метрологом",
                manager_fio="Мегрелишвили Михаил Эмзарович",
                meeting_type="Отчетное",
                participant_fios=["Хозуян Иван Владимирович"],
            )
        )

    assert result.similar_found is True
    assert result.requires_user_decision is True
    assert result.similar_topic is not None
    assert result.similar_topic.ref_key == "topic-1"
    assert result.similarity_score == 0.91
    assert "использовать" in result.message.lower()


@pytest.mark.asyncio
async def test_check_similar_not_found_asks_for_new_fields() -> None:
    service = MeetingTopicService()

    with (
        patch(
            "app.services.meeting_topic_service.asyncio.to_thread",
            new=AsyncMock(return_value=(object(), "manager-ref", "Manager")),
        ),
        patch(
            "app.services.meeting_topic_service.find_similar_topic_for_manager_async",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await service.check_similar(
            MeetingTopicCheckSimilarRequest(
                description="Новая уникальная тема",
                manager_fio="Manager",
            )
        )

    assert result.similar_found is False
    assert result.requires_user_decision is True
    assert "description" in result.required_fields
    assert "создания новой" in result.message.lower()


@pytest.mark.asyncio
async def test_resolve_use_existing() -> None:
    service = MeetingTopicService()
    topic = MeetingTopicSummaryRead(
        ref_key="topic-1",
        code="000009370",
        description="Тема",
        meeting_type="Отчетное",
    )

    with patch(
        "app.services.meeting_topic_service._load_topic_detail",
        new=AsyncMock(return_value=topic),
    ):
        result = await service.resolve(
            MeetingTopicResolveRequest(
                decision="use_existing",
                existing_topic_ref_key="topic-1",
            )
        )

    assert result.used_existing is True
    assert result.created is False
    assert result.topic.ref_key == "topic-1"


@pytest.mark.asyncio
async def test_resolve_create_new_skips_similarity() -> None:
    service = MeetingTopicService()

    with patch(
        "app.services.meeting_topic_service.asyncio.to_thread",
        new=AsyncMock(
            return_value={
                "created": True,
                "dry_run": False,
                "participants_count": 2,
                "participants": [],
                "topic": {
                    "ref_key": "new-topic",
                    "code": "000009999",
                    "description": "Новая тема",
                },
                "message": "Создана тема совещания №000009999.",
            }
        ),
    ) as create_mock:
        result = await service.resolve(
            MeetingTopicResolveRequest(
                decision="create_new",
                description="Новая тема",
                manager_fio="Manager",
                meeting_type="Отчетное",
            )
        )

    assert result.created is True
    assert result.used_existing is False
    assert create_mock.await_args.kwargs["skip_similarity_check"] is True


@pytest.mark.asyncio
async def test_load_topic_detail_normalizes_raw_odata_row() -> None:
    raw_row = {
        "Ref_Key": "8296c4b9-3e91-49f8-b957-2571d9aacec7",
        "Code": "000010399",
        "Description": "тест",
        "ВидСовещания": "Отчетное",
        "DeletionMark": False,
    }

    with (
        patch(
            "app.services.meeting_topic_service.fetch_topic_by_key",
            return_value=raw_row,
        ),
        patch(
            "app.services.meeting_topic_service._load_topic_participants",
            new=AsyncMock(return_value=[]),
        ),
    ):
        topic = await _load_topic_detail("8296c4b9-3e91-49f8-b957-2571d9aacec7")

    assert topic.ref_key == "8296c4b9-3e91-49f8-b957-2571d9aacec7"
    assert topic.code == "000010399"
    assert topic.description == "тест"
    assert topic.meeting_type == "Отчетное"
