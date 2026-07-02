from app.tools.onec.meeting_topics_by_manager import (
    group_topics_by_manager,
    manager_fio_from_topic,
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
