from __future__ import annotations

from app.services.employee_sync_service import _parse_full_name, _sync_email


def test_parse_full_name_three_parts():
    names = _parse_full_name("Давлетов Руслан Игоревич")
    assert names == {
        "last_name": "Давлетов",
        "first_name": "Руслан",
        "middle_name": "Игоревич",
    }


def test_parse_full_name_two_parts():
    names = _parse_full_name("Иванов Иван")
    assert names["last_name"] == "Иванов"
    assert names["first_name"] == "Иван"
    assert names["middle_name"] is None


def test_sync_email_is_stable():
    assert _sync_email("ABCD-1234") == "1c+abcd-1234@enterprise.sync.local"
