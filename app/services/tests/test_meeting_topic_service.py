from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.meeting_topic import (
    MeetingTopicCheckSimilarRequest,
    MeetingTopicParticipantRead,
    MeetingTopicResolveRequest,
    MeetingTopicSummaryRead,
)
from app.services.meeting_topic_service import (
    MeetingTopicService,
    MeetingTopicServiceError,
    _load_topic_detail,
    _preview_memo_participants_vs_topic,
    is_newly_created_meeting_topic,
    sync_new_topic_closed_date_after_scheduling,
)


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
        "similarity_breakdown": {"topic": 0.95, "participants": 0.0, "details": 0.8},
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
        patch(
            "app.services.meeting_topic_service._preview_memo_participants_vs_topic",
            new=AsyncMock(return_value=([], [], 1.0)),
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
    assert result.similarity_breakdown is not None
    assert result.similarity_breakdown.participants == 1.0
    assert result.missing_participants == []
    assert result.unresolved_participants == []
    assert "использовать" in result.message.lower()


@pytest.mark.asyncio
async def test_check_similar_suggests_missing_participants_from_memo() -> None:
    service = MeetingTopicService()
    similar_topic = {
        "ref_key": "topic-dpi",
        "code": "000007712",
        "description": "ДПИ сектора внедрения ИИ",
        "meeting_type": "Внеплановое",
        "similarity_score": 1.0,
        "similarity_method": "text",
        "similarity_breakdown": {"topic": 1.0, "participants": 0.0, "details": 0.0},
        "is_active": True,
    }
    missing = [
        MeetingTopicParticipantRead(
            participant_ref_key="person-nikitaev",
            fio="Никитаев Алексей Вячеславович",
        )
    ]

    with (
        patch(
            "app.services.meeting_topic_service.asyncio.to_thread",
            new=AsyncMock(
                return_value=(
                    object(),
                    "manager-ref",
                    "Соломичева Светлана Викторовна",
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
                        participant_ref_key="person-yasyrov",
                        fio="Ясыров Богдан Джумазаевич",
                    )
                ]
            ),
        ),
        patch(
            "app.services.meeting_topic_service._preview_memo_participants_vs_topic",
            new=AsyncMock(return_value=(missing, [], 0.25)),
        ) as preview_mock,
    ):
        result = await service.check_similar(
            MeetingTopicCheckSimilarRequest(
                description="ДПИ ИИ",
                manager_fio="Соломичева Светлана Викторовна",
                meeting_type="Отчетное",
                initiator_fio="Комарькова Анастасия Эдуардовна",
                participant_fios=[
                    "Никитаев Алексей Вячеславович",
                    "Ясыров Богдан Джумазаевич",
                ],
            )
        )

    assert result.similar_found is True
    assert len(result.missing_participants) == 1
    assert result.missing_participants[0].fio == "Никитаев Алексей Вячеславович"
    assert result.similarity_breakdown is not None
    assert result.similarity_breakdown.participants == 0.25
    assert "никитаев" in result.message.casefold()
    assert "добавлены в тему" in result.message.casefold()
    preview_mock.assert_awaited_once()
    preview_kwargs = preview_mock.await_args.kwargs
    assert "Никитаев Алексей Вячеславович" in preview_kwargs["participant_fios"]
    assert "Комарькова Анастасия Эдуардовна" in preview_kwargs["participant_fios"]


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
    assert "participant_fios" in result.required_fields
    assert "создания новой" in result.message.lower()


@pytest.mark.asyncio
async def test_preview_memo_participants_vs_topic_uses_dry_run_merge() -> None:
    with patch(
        "app.services.meeting_topic_service.asyncio.to_thread",
        new=AsyncMock(
            return_value={
                "added": [
                    {
                        "participant_ref_key": "person-new",
                        "fio": "Никитаев Алексей Вячеславович",
                    }
                ],
                "skipped_already_in_topic": [
                    {
                        "participant_ref_key": "person-old",
                        "fio": "Ясыров Богдан Джумазаевич",
                    }
                ],
                "not_found_in_1c": [],
            }
        ),
    ):
        missing, unresolved, score = await _preview_memo_participants_vs_topic(
            topic_ref_key="topic-1",
            topic_participants=[
                MeetingTopicParticipantRead(
                    participant_ref_key="person-old",
                    fio="Ясыров Богдан Джумазаевич",
                )
            ],
            participant_fios=[
                "Никитаев Алексей Вячеславович",
                "Ясыров Богдан Джумазаевич",
            ],
        )

    assert len(missing) == 1
    assert missing[0].fio == "Никитаев Алексей Вячеславович"
    assert missing[0].participant_ref_key == "person-new"
    assert unresolved == []
    assert score == pytest.approx(0.5)


def test_missing_participants_by_fio_ignores_people_already_in_topic() -> None:
    from app.services.meeting_topic_service import _missing_participants_by_fio

    missing = _missing_participants_by_fio(
        [
            "Никитаев Алексей Вячеславович",
            "Ясыров Богдан Джумазаевич",
            "Соломичева Светлана Викторовна",
        ],
        [
            MeetingTopicParticipantRead(
                participant_ref_key="p1",
                fio="Ясыров Богдан Джумазаевич",
            ),
            MeetingTopicParticipantRead(
                participant_ref_key="p2",
                fio="Соломичева Светлана Викторовна",
            ),
        ],
    )

    assert [item.fio for item in missing] == ["Никитаев Алексей Вячеславович"]


@pytest.mark.asyncio
async def test_preview_keeps_missing_even_when_fio_not_found_in_1c() -> None:
    with patch(
        "app.services.meeting_topic_service.asyncio.to_thread",
        new=AsyncMock(
            return_value={
                "added": [],
                "skipped_already_in_topic": [
                    {
                        "participant_ref_key": "person-old",
                        "fio": "Ясыров Богдан Джумазаевич",
                    }
                ],
                "not_found_in_1c": [{"fio": "Никитаев Алексей Вячеславович"}],
            }
        ),
    ):
        missing, unresolved, score = await _preview_memo_participants_vs_topic(
            topic_ref_key="topic-1",
            topic_participants=[
                MeetingTopicParticipantRead(
                    participant_ref_key="person-old",
                    fio="Ясыров Богдан Джумазаевич",
                )
            ],
            participant_fios=[
                "Никитаев Алексей Вячеславович",
                "Ясыров Богдан Джумазаевич",
            ],
        )

    assert [item.fio for item in missing] == ["Никитаев Алексей Вячеславович"]
    assert [item.fio for item in unresolved] == ["Никитаев Алексей Вячеславович"]
    assert score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_resolve_use_existing() -> None:
    service = MeetingTopicService()
    topic = MeetingTopicSummaryRead(
        ref_key="topic-1",
        code="000009370",
        description="Тема",
        meeting_type="Отчетное",
        participants=[
            MeetingTopicParticipantRead(
                participant_ref_key="p-1",
                fio="Хозуян Иван Владимирович",
            )
        ],
    )

    with (
        patch(
            "app.services.meeting_topic_service.asyncio.to_thread",
            new=AsyncMock(
                return_value={
                    "added": [
                        {
                            "participant_ref_key": "p-2",
                            "fio": "Новый Участник",
                        }
                    ]
                }
            ),
        ),
        patch(
            "app.services.meeting_topic_service._load_topic_detail",
            new=AsyncMock(return_value=topic),
        ),
    ):
        result = await service.resolve(
            MeetingTopicResolveRequest(
                decision="use_existing",
                existing_topic_ref_key="topic-1",
                manager_fio="Менеджер",
                initiator_fio="Инициатор",
                participant_fios=["Новый Участник"],
            )
        )

    assert result.used_existing is True
    assert result.created is False
    assert result.topic.ref_key == "topic-1"
    assert len(result.added_participants) == 1
    assert result.added_participants[0].fio == "Новый Участник"
    assert "добавлены участники" in result.message.lower()


@pytest.mark.asyncio
async def test_resolve_use_existing_requires_participants() -> None:
    service = MeetingTopicService()
    topic = MeetingTopicSummaryRead(
        ref_key="topic-1",
        code="000009370",
        description="Тема",
        meeting_type="Отчетное",
        participants=[],
    )

    with patch(
        "app.services.meeting_topic_service._load_topic_detail",
        new=AsyncMock(return_value=topic),
    ):
        with pytest.raises(MeetingTopicServiceError) as exc_info:
            await service.resolve(
                MeetingTopicResolveRequest(
                    decision="use_existing",
                    existing_topic_ref_key="topic-1",
                )
            )

    assert "участники" in str(exc_info.value).lower()


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
                participant_fios=["Хозуян Иван Владимирович"],
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


def test_is_newly_created_meeting_topic() -> None:
    assert is_newly_created_meeting_topic({"created": True, "used_existing": False}) is True
    assert is_newly_created_meeting_topic({"created": False, "used_existing": True}) is False
    assert is_newly_created_meeting_topic(None) is False


@pytest.mark.asyncio
async def test_sync_new_topic_closed_date_after_scheduling() -> None:
    topic = {
        "ref_key": "topic-1",
        "created": True,
        "used_existing": False,
    }
    with patch(
        "app.services.meeting_topic_service.update_meeting_topic_closed_date",
        return_value={"Ref_Key": "topic-1"},
    ) as update_mock:
        result = await sync_new_topic_closed_date_after_scheduling(
            topic,
            "2026-07-22T13:00:00+03:00",
        )

    assert result == {"ref_key": "topic-1", "closed_date": "2026-08-05T00:00:00"}
    update_mock.assert_called_once_with("topic-1", "2026-08-05T00:00:00")


@pytest.mark.asyncio
async def test_validate_topic_ref_key_rejects_missing_topic() -> None:
    service = MeetingTopicService()
    result = await service.validate_topic_ref_key("00000000-0000-0000-0000-000000000000")
    assert result.valid is False
    assert "не найдена" in (result.reason or "").casefold()


@pytest.mark.asyncio
async def test_validate_topic_ref_key_accepts_topic_with_participants() -> None:
    service = MeetingTopicService()
    with (
        patch(
            "app.services.meeting_topic_service.fetch_topic_by_key",
            return_value={
                "Ref_Key": "topic-1",
                "Code": "000010399",
                "Description": "Новая тема",
                "DeletionMark": False,
            },
        ),
        patch(
            "app.services.meeting_topic_service._load_topic_participants",
            new=AsyncMock(
                return_value=[
                    MeetingTopicParticipantRead(
                        participant_ref_key="person-1",
                        fio="Комарькова Анастасия Эдуардовна",
                    )
                ]
            ),
        ),
    ):
        result = await service.validate_topic_ref_key("topic-1")

    assert result.valid is True
    assert result.topic is not None
    assert result.topic.code == "000010399"


@pytest.mark.asyncio
async def test_sync_new_topic_closed_date_skips_existing_topic() -> None:
    result = await sync_new_topic_closed_date_after_scheduling(
        {"ref_key": "topic-1", "created": False, "used_existing": True},
        "2026-07-22T13:00:00+03:00",
    )
    assert result is None
