from datetime import date, datetime

from app.tools.onec.get_porucheniya import (
    build_document_period_filter,
    build_manager_filter,
    collect_protocol_lookup_keys,
    compute_priority,
    filter_protocol_documents_by_manager_fio,
    filter_protocol_tasks_by_manager,
    filter_rows_by_manager,
    fio_matches,
    flatten_protocol_tasks,
    group_porucheniya_documents,
    group_protocol_documents,
    parse_input_date,
    resolve_porucheniya_period,
    row_has_file,
)


def test_parse_input_date_accepts_iso_string() -> None:
    assert parse_input_date("2026-03-01") == parse_input_date("2026-03-01T12:00:00")


def test_resolve_porucheniya_period_defaults_to_yesterday() -> None:
    today = date(2026, 6, 18)
    start, end = resolve_porucheniya_period(None, None, today=today)
    assert start == end == date(2026, 6, 17)


def test_resolve_porucheniya_period_single_end_date() -> None:
    start, end = resolve_porucheniya_period(None, "2026-05-20", today=date(2026, 6, 18))
    assert start == end == date(2026, 5, 20)


def test_compute_priority_marks_due_today_as_high() -> None:
    now = datetime(2026, 6, 18, 15, 0, 0)
    due = datetime(2026, 6, 18, 0, 0, 0)
    assert (
        compute_priority(
            due_date=due,
            confirmed=False,
            completed=False,
            has_file=True,
            manager="",
            now=now,
        )
        == "Высокий"
    )


def test_compute_priority_overdue_without_file_becomes_high() -> None:
    now = datetime(2026, 6, 18, 15, 0, 0)
    due = datetime(2026, 6, 10, 0, 0, 0)
    assert (
        compute_priority(
            due_date=due,
            confirmed=False,
            completed=True,
            has_file=False,
            manager="",
            now=now,
        )
        == "Высокий"
    )


def test_compute_priority_critical_manager() -> None:
    now = datetime(2026, 6, 18, 15, 0, 0)
    due = datetime(2026, 7, 1, 0, 0, 0)
    assert (
        compute_priority(
            due_date=due,
            confirmed=False,
            completed=False,
            has_file=True,
            manager="Амураль Игорь Борисович",
            now=now,
        )
        == "Критический"
    )


def test_fio_matches_ignores_case_and_yo() -> None:
    assert fio_matches("Иванов Иван Иванович", "иванов иван иванович")
    assert fio_matches("Семёнов", "Семенов")
    assert not fio_matches("Иванов", "Петров")


def test_build_document_period_filter_uses_document_date() -> None:
    expr = build_document_period_filter(parse_input_date("2026-05-01"), parse_input_date("2026-06-18"))
    assert "Date ge datetime'2026-05-01T00:00:00'" in expr
    assert "Date le datetime'2026-06-18T23:59:59'" in expr
    assert "DeletionMark eq false" in expr


def test_build_manager_filter_joins_keys() -> None:
    joined = build_manager_filter({"aaa", "bbb"})
    assert joined.startswith("(") and joined.endswith(")")
    assert "Руководитель_Key eq guid'aaa'" in joined
    assert "Руководитель_Key eq guid'bbb'" in joined
    single = build_manager_filter({"11111111-1111-1111-1111-111111111111"})
    assert single == "Руководитель_Key eq guid'11111111-1111-1111-1111-111111111111'"


def test_filter_rows_by_manager() -> None:
    rows = [
        {"Ref_Key": "doc-1", "LineNumber": 1},
        {"Ref_Key": "doc-2", "LineNumber": 1},
    ]
    parents = {
        "doc-1": {"Руководитель_Key": "mgr-key"},
        "doc-2": {"Руководитель_Key": "other-key"},
    }
    filtered = filter_rows_by_manager(rows, parents, {"mgr-key"}, limit=10)
    assert len(filtered) == 1
    assert filtered[0]["Ref_Key"] == "doc-1"


def test_filter_protocol_tasks_by_manager() -> None:
    rows = [{"Протокол_Key": "proto-1", "ИдентификаторЗадачи": "task-1"}]
    protocols = {"proto-1": {"Руководитель_Key": "mgr-key"}}
    filtered = filter_protocol_tasks_by_manager(rows, protocols, {"mgr-key"}, limit=10)
    assert len(filtered) == 1


def test_row_has_file_detects_base64_payload() -> None:
    assert row_has_file({"Файл_Base64Data": "abc"}) is True
    assert row_has_file({"Файл_Base64Data": "", "Файл": "0001-01-01T00:00:00"}) is False


def test_group_porucheniya_documents_builds_tasks_under_document() -> None:
    now = datetime(2026, 6, 18, 15, 0, 0)
    parent = {
        "Ref_Key": "doc-1",
        "Number": "АСТ00-00039",
        "Date": "2026-05-29T15:12:24",
        "ОЧем": "На основании протокола",
        "Статус": "ВРаботе",
        "Основание": "Поручение Амураль И.Б.",
        "Руководитель_Key": "mgr-key",
        "СекретарьРК_Key": "sec-key",
    }
    rows = [
        {
            "Ref_Key": "doc-1",
            "LineNumber": "1",
            "Мероприятие": "Задача 1",
            "СрокИсполнения": "2026-06-02T00:00:00",
            "ОтветственноеЛицо_Key": "resp-1",
        },
        {
            "Ref_Key": "doc-1",
            "LineNumber": "2",
            "Мероприятие": "Задача 2",
            "СрокИсполнения": "2026-06-16T00:00:00",
            "ОтветственноеЛицо_Key": "resp-2",
        },
    ]
    users = {
        "mgr-key": {"Description": "Амураль Игорь Борисович"},
        "sec-key": {"Description": "Ильченко Екатерина Александровна"},
        "resp-1": {"Description": "Исполнитель 1"},
        "resp-2": {"Description": "Исполнитель 2"},
    }
    documents = group_porucheniya_documents(
        {"doc-1": parent},
        rows,
        users=users,
        persons={},
        departments_by_responsible={
            "resp-1": "Департамент цифровизации",
            "resp-2": "Управление проектами",
        },
        now=now,
    )
    assert len(documents) == 1
    document = documents[0]
    assert document["document_number"] == "АСТ00-00039"
    assert document["manager"] == "Амураль Игорь Борисович"
    assert document["reviewer"] == "Ильченко Екатерина Александровна"
    assert document["tasks_count"] == 2
    assert document["tasks"][0]["item_type"] == "poruchenie_task"
    activities = {task["activity"] for task in document["tasks"]}
    assert activities == {"Задача 1", "Задача 2"}
    departments = {task["department"] for task in document["tasks"]}
    assert departments == {"Департамент цифровизации", "Управление проектами"}


def test_collect_protocol_lookup_keys_includes_header_without_register_rows() -> None:
    protocols = {
        "proto-1": {
            "Руководитель_Key": "mgr-key",
            "Ответственный_Key": "reviewer-key",
            "ТемаСовещания_Key": "topic-1",
        }
    }
    user_keys, person_keys, topic_keys = collect_protocol_lookup_keys([], protocols)
    assert user_keys == {"mgr-key", "reviewer-key"}
    assert person_keys == {"reviewer-key"}
    assert topic_keys == {"topic-1"}


def test_group_protocol_documents_without_tasks_keeps_manager() -> None:
    now = datetime(2026, 6, 18, 15, 0, 0)
    protocol = {
        "Ref_Key": "proto-1",
        "Number": "ЗДК_040_О_196",
        "Date": "2026-06-18T10:00:00",
        "Статус": "ВРаботе",
        "Руководитель_Key": "mgr-key",
        "Ответственный_Key": "reviewer-key",
        "ТемаСовещания_Key": "topic-1",
    }
    users = {
        "mgr-key": {"Description": "Арсуноев Михаил Магомедович"},
        "reviewer-key": {"Description": "Арсуноев Михаил Магомедович"},
    }
    topics = {"topic-1": {"Description": "Заседание ЗДК"}}
    documents = group_protocol_documents(
        {"proto-1": protocol},
        [],
        users=users,
        persons={},
        topics=topics,
        departments_by_responsible={},
        now=now,
    )
    assert len(documents) == 1
    document = documents[0]
    assert document["manager"] == "Арсуноев Михаил Магомедович"
    assert document["tasks_count"] == 0
    filtered = filter_protocol_documents_by_manager_fio(
        documents,
        "Арсуноев Михаил Магомедович",
    )
    assert len(filtered) == 1


def test_group_protocol_documents_builds_tasks_under_document() -> None:
    now = datetime(2026, 6, 18, 15, 0, 0)
    protocol = {
        "Ref_Key": "proto-1",
        "Number": "ПСД_001_О_102",
        "Date": "2026-05-20T10:00:00",
        "Статус": "ВРаботе",
        "Руководитель_Key": "mgr-key",
        "Ответственный_Key": "reviewer-key",
        "ТемаСовещания_Key": "topic-1",
    }
    rows = [
        {
            "Протокол_Key": "proto-1",
            "ИдентификаторЗадачи": "task-1",
            "Задача": "Подготовить материалы",
            "СрокИсполнения": "2026-06-10T00:00:00",
            "Ответственный_Key": "resp-1",
            "ТемаСовещания_Key": "topic-1",
        },
        {
            "Протокол_Key": "proto-1",
            "ИдентификаторЗадачи": "task-2",
            "Задача": "Согласовать повестку",
            "СрокИсполнения": "2026-06-15T00:00:00",
            "Ответственный_Key": "resp-2",
            "ТемаСовещания_Key": "topic-1",
        },
    ]
    users = {
        "mgr-key": {"Description": "Амураль Игорь Борисович"},
        "reviewer-key": {"Description": "Ильченко Екатерина Александровна"},
        "resp-1": {"Description": "Исполнитель 1"},
        "resp-2": {"Description": "Исполнитель 2"},
    }
    topics = {"topic-1": {"Description": "Заседание ПСД"}}
    documents = group_protocol_documents(
        {"proto-1": protocol},
        rows,
        users=users,
        persons={},
        topics=topics,
        departments_by_responsible={"resp-1": "Департамент цифровизации"},
        now=now,
    )
    assert len(documents) == 1
    document = documents[0]
    assert document["document_number"] == "ПСД_001_О_102"
    assert document["topic"] == "Заседание ПСД"
    assert document["reviewer"] == "Ильченко Екатерина Александровна"
    assert document["tasks_count"] == 2
    assert document["tasks"][0]["item_type"] == "protocol_task"
    assert flatten_protocol_tasks(documents)[0]["document_number"] == "ПСД_001_О_102"
