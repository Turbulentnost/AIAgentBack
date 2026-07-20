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
