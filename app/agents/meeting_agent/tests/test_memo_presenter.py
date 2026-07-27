from unittest.mock import Mock, patch

from app.agents.meeting_agent.memo_presenter import (
    _collect_participant_rows,
    _participants_count_from_header,
    _resolve_participants,
    build_queue_item_from_row,
    resolve_meeting_schedule,
)


def test_resolve_meeting_schedule_combines_desired_date_with_time_only_fields() -> None:
    header = {
        "ЖелаемаяДатаПроведенияСовещания": "2026-06-19T00:00:00",
        "ВремяНачалаСовещания": "0001-01-01T11:00:00",
        "ВремяОкончанияСовещания": "0001-01-01T11:20:00",
    }

    start, end = resolve_meeting_schedule(header)

    assert start is not None
    assert end is not None
    assert start.strftime("%Y-%m-%d %H:%M") == "2026-06-19 11:00"
    assert end.strftime("%Y-%m-%d %H:%M") == "2026-06-19 11:20"


def test_resolve_meeting_schedule_combines_desired_date_with_excel_time_fields() -> None:
    header = {
        "ЖелаемаяДатаПроведенияСовещания": "2026-01-23 00:00:00",
        "ВремяНачалаСовещания": "01.01.0001 11:00:00",
        "ВремяОкончанияСовещания": "01.01.0001 11:30:00",
    }

    start, end = resolve_meeting_schedule(header)

    assert start is not None
    assert end is not None
    assert start.strftime("%Y-%m-%d %H:%M") == "2026-01-23 11:00"
    assert end.strftime("%Y-%m-%d %H:%M") == "2026-01-23 11:30"


def test_build_queue_item_from_row_resolves_location_label() -> None:
    location_key = "df88b4e5-47aa-11f1-97f7-6cb31113810e"
    row = {
        "Ref_Key": "37da8ed8-6b19-11f1-9825-6cb31113810e",
        "Number": "000009938",
        "Date": "2026-06-18T16:25:57",
        "Статус": "НеСогласована",
        "ТемаСовещания": "Тестовая тема",
        "ЖелаемаяДатаПроведенияСовещания": "2026-06-19T00:00:00",
        "ВремяНачалаСовещания": "0001-01-01T11:00:00",
        "ВремяОкончанияСовещания": "0001-01-01T11:20:00",
        "МестоПроведенияСовещания": location_key,
    }

    item = build_queue_item_from_row(
        row,
        location_labels={location_key.lower(): "Кабинет 201"},
    )

    assert item["scheduled_label"] == "19.06.2026, 11:00–11:20"
    assert item["location"] == "Кабинет 201"
    assert item["document_date"] == "18.06.2026"
    assert item["document_date_label"] == "18.06.2026"


def test_build_queue_item_from_row_includes_series_planning_fields() -> None:
    row = {
        "Ref_Key": "37da8ed8-6b19-11f1-9825-6cb31113810e",
        "Number": "000011832",
        "Date": "2026-07-23T09:40:53",
        "Статус": "НеСогласована",
        "ТемаСовещания": "тест",
        "ЖелаемаяДатаПроведенияСовещания": "2026-07-24T00:00:00",
        "ВремяНачалаСовещания": "0001-01-01T13:00:00",
        "ВремяОкончанияСовещания": "0001-01-01T13:20:00",
        "ТекстСлужебнойЗаписки": (
            "Прошу распланировать совещания на две недели ежедневно с 13:15-14:00"
        ),
    }

    item = build_queue_item_from_row(row)

    assert item["series_detected"] is True
    assert item["series_recurrence_label"] is not None
    assert "ежедневно" in item["series_recurrence_label"]


def test_header_with_people_keys_fetches_full_header_when_date_missing() -> None:
    row = {
        "Ref_Key": "37da8ed8-6b19-11f1-9825-6cb31113810e",
        "Ответственный_Key": "11111111-1111-1111-1111-111111111111",
        "РуководительСовещания_Key": "22222222-2222-2222-2222-222222222222",
        "ТекстСлужебнойЗаписки": "уже есть текст",
    }
    full_header = {
        "Ref_Key": row["Ref_Key"],
        "Date": "2026-07-08T09:49:00",
        "Number": "000011087",
    }

    with patch(
        "app.agents.meeting_agent.memo_presenter.fetch_document_header",
        return_value=full_header,
    ) as fetch_header:
        from app.agents.meeting_agent.memo_presenter import _header_with_people_keys

        merged = _header_with_people_keys(row, session=object(), config=object())

    fetch_header.assert_called_once()
    assert merged["Date"] == "2026-07-08T09:49:00"


def test_header_with_people_keys_fetches_when_memo_text_missing() -> None:
    row = {
        "Ref_Key": "37da8ed8-6b19-11f1-9825-6cb31113810e",
        "Date": "2026-07-08T09:49:00",
        "Ответственный_Key": "11111111-1111-1111-1111-111111111111",
        "РуководительСовещания_Key": "22222222-2222-2222-2222-222222222222",
    }
    full_header = {
        "Ref_Key": row["Ref_Key"],
        "ТекстСлужебнойЗаписки": "прошу назначить совещание",
    }

    with patch(
        "app.agents.meeting_agent.memo_presenter.fetch_document_header",
        return_value=full_header,
    ) as fetch_header:
        from app.agents.meeting_agent.memo_presenter import _header_with_people_keys

        merged = _header_with_people_keys(row, session=object(), config=object())

    fetch_header.assert_called_once()
    assert merged["ТекстСлужебнойЗаписки"] == "прошу назначить совещание"


def test_normalize_dashboard_item_formats_document_date() -> None:
    from app.agents.meeting_agent.dashboard import normalize_dashboard_item

    item = normalize_dashboard_item(
        {
            "ref_key": "111",
            "number": "000011087",
            "Date": "2026-07-08T09:49:00",
        }
    )

    assert item["document_date"] == "08.07.2026"
    assert item["document_date_label"] == "08.07.2026"


def test_build_queue_item_counts_inline_participants_without_names() -> None:
    row = {
        "Ref_Key": "37da8ed8-6b19-11f1-9825-6cb31113810e",
        "Number": "000009935",
        "СписокУчастников": [
            {"LineNumber": 1, "Участник_Key": "11111111-1111-1111-1111-111111111111"},
            {"LineNumber": 2, "Участник_Key": "22222222-2222-2222-2222-222222222222"},
        ],
    }

    item = build_queue_item_from_row(row)

    assert item["participants_count"] == 2


def test_resolve_participants_loads_names_from_physical_persons() -> None:
    participant_key = "11111111-1111-1111-1111-111111111111"
    document = {
        "header": {
            "СписокУчастников": [
                {"LineNumber": 1, "Участник_Key": participant_key},
            ]
        }
    }

    class FakeSession:
        pass

    with patch(
        "app.agents.meeting_agent.memo_presenter._load_users_by_keys",
        return_value={},
    ):
        with patch(
            "app.agents.meeting_agent.memo_presenter.load_persons_for_keys",
            return_value={
                participant_key: {
                    "Ref_Key": participant_key,
                    "Description": "Иванов Иван Иванович",
                }
            },
        ):
            participants = _resolve_participants(FakeSession(), Mock(), document)

    assert len(participants) == 1
    assert participants[0]["full_name"] == "Иванов Иван Иванович"


def test_collect_participant_rows_skips_agenda_tabular_section() -> None:
    document = {
        "header": {},
        "tabular_sections": {
            "СписокУчастников": [
                {"LineNumber": "1", "Участник_Key": "11111111-1111-1111-1111-111111111111"},
            ],
            "ПланСовещания": [
                {"LineNumber": "1", "Задача": "Пункт повестки"},
            ],
        },
    }

    rows = _collect_participant_rows(document)

    assert len(rows) == 1
    assert rows[0]["Участник_Key"] == "11111111-1111-1111-1111-111111111111"

