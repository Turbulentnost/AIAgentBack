from app.services.google_sheets_client import (
    _resolve_preferred_sheet,
    is_china_worksheet_title,
)


def test_is_china_worksheet_title_accepts_renamed_sheets() -> None:
    assert is_china_worksheet_title("ИТЦ В РАБОТЕ")
    assert is_china_worksheet_title("Гонконг В РАБОТЕ")
    assert is_china_worksheet_title("ТАМОЖНЯ") is False


def test_resolve_preferred_sheet_falls_back_to_renamed_work_sheet() -> None:
    sheets = [
        {"title": "ТАМОЖНЯ", "gid": 1},
        {"title": "Гонконг В РАБОТЕ", "gid": 2},
    ]
    resolved = _resolve_preferred_sheet(sheets, sheet_title="ИТЦ В РАБОТЕ")
    assert resolved is not None
    assert resolved["title"] == "Гонконг В РАБОТЕ"
