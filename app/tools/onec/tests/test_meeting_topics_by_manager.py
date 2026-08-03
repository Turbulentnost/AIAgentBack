from datetime import date

from app.tools.onec.meeting_topics_by_manager import (
    filter_topics_for_manager_similarity,
    group_topics_by_manager,
    manager_fio_from_topic,
    topic_belongs_to_manager,
    topic_matches_manager_fio,
)


def test_group_topics_by_manager() -> None:
    topics = [
        {
            "ref_key": "a",
            "description": "Тема 1",
            "manager": "Иванов Иван Иванович",
            "keys": {"manager": "user-1"},
            "meeting_type": "Плановое",
            "is_active": True,
        },
        {
            "ref_key": "b",
            "description": "Тема 2",
            "manager": "Иванов Иван Иванович",
            "keys": {"manager": "user-1"},
            "meeting_type": "Отчетное",
            "is_active": True,
        },
        {
            "ref_key": "c",
            "description": "Тема 3",
            "manager": "Петров Петр Петрович",
            "keys": {"manager": "user-2"},
            "meeting_type": "Плановое",
            "is_active": False,
        },
    ]

    groups = group_topics_by_manager(topics)

    assert len(groups) == 2
    assert groups[0]["manager_fio"] == "Иванов Иван Иванович"
    assert groups[0]["topics_count"] == 2
    assert groups[1]["manager_fio"] == "Петров Петр Петрович"


def test_topic_matches_manager_fio_partial() -> None:
    topic = {"manager": "Соломичева Светлана Викторовна"}
    assert topic_matches_manager_fio(topic, "Соломичева Светлана")
    assert not topic_matches_manager_fio(topic, "Комарькова")


def test_manager_fio_from_topic_fallback() -> None:
    assert manager_fio_from_topic({}) == "—"


def test_filter_topics_for_manager_similarity_keeps_active_manager_topics_only() -> None:
    topics = [
        {
            "ref_key": "active",
            "closed_date": "2026-12-31T00:00:00",
            "is_active": True,
            "keys": {"manager": "manager-1"},
        },
        {
            "ref_key": "closed-today",
            "closed_date": "2026-07-22T00:00:00",
            "is_active": False,
            "keys": {"manager": "manager-1"},
        },
        {
            "ref_key": "other-manager",
            "closed_date": "2026-12-31T00:00:00",
            "is_active": True,
            "keys": {"manager": "manager-2"},
        },
    ]

    filtered = filter_topics_for_manager_similarity(
        topics,
        manager_ref_key="manager-1",
        today=date(2026, 7, 22),
    )

    assert [topic["ref_key"] for topic in filtered] == ["active"]


def test_topic_belongs_to_manager() -> None:
    topic = {"keys": {"manager": "Manager-Ref"}}
    assert topic_belongs_to_manager(topic, "manager-ref")
    assert not topic_belongs_to_manager(topic, "other-ref")
