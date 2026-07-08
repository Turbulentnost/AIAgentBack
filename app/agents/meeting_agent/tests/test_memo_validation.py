from __future__ import annotations

from app.agents.meeting_agent.memo_validation import validate_meeting_memo_document


def test_validate_meeting_memo_missing_document() -> None:
    issues = validate_meeting_memo_document(None)
    assert len(issues) == 1
    assert issues[0].field == "memo"


def test_validate_meeting_memo_ok() -> None:
    document = {
        "memo": {
            "Ref_Key": "00000000-0000-0000-0000-000000000001",
            "Number": "000000001",
            "Date": "2026-06-01",
            "ТемаСлужебнойЗаписки": "Организация совещаний (регл.)",
            "Направление": "Управление делами",
            "ТемаСовещания": "Еженедельное совещание",
            "РуководительСовещания_Key": "22222222-2222-2222-2222-222222222222",
            "ЦельПланаСовещания": "Согласовать план",
            "Приоритет_Key": "33333333-3333-3333-3333-333333333333",
            "ЖелаемаяДатаПроведенияСовещания": "2026-06-10T00:00:00",
            "ВремяНачалаСовещания": "2026-06-10T10:00:00",
            "ВремяОкончанияСовещания": "2026-06-10T11:00:00",
            "МестоПроведенияСовещания": "Переговорная 1",
            "ПланСовещания": [{"Задача": "Обсудить статус"}],
        },
        "participants": [{"Description": "Иванов Иван Иванович"}],
    }
    issues = validate_meeting_memo_document(document)
    assert issues == []


def test_validate_meeting_memo_missing_participants() -> None:
    document = {
        "memo": {
            "Ref_Key": "00000000-0000-0000-0000-000000000001",
            "Number": "000000001",
        },
        "participants": [],
    }
    issues = validate_meeting_memo_document(document)
    assert any(item.field == "participants" for item in issues)
