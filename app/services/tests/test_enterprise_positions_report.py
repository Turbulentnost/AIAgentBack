from pathlib import Path

from app.services.enterprise_positions_report import (
    is_director_by_fio,
    is_director_position_title,
    lookup_positions_by_fio,
    parse_enterprise_positions_report,
)
from app.services.meeting_attendee_priority import (
    PRIORITY_DIRECTOR,
    PRIORITY_PARTICIPANT,
    resolve_priority_role,
)
from app.services.meeting_attendees import collect_attendees_from_detail

SAMPLE_REPORT = """
Найдено назначений: 4

Оргструктура: Руководство ГК
  Председатель совета директоров
    - Амураль Игорь Борисович с 2017-11-01
  Директор
    - Донцова Анна Егоровна с 2025-10-02

Оргструктура: Обособленное подразделение / Коммерческая служба
  Начальник отдела
    - Яковлева Светлана Викторовна с 2025-03-22
  Помощник Председателя совета директоров
    - Комарькова Анастасия Эдуардовна с 2024-01-01
"""


def test_parse_enterprise_positions_report() -> None:
    rows = parse_enterprise_positions_report(SAMPLE_REPORT)
    assert len(rows) == 4
    assert rows[0].fio == "Амураль Игорь Борисович"
    assert rows[0].position == "Председатель совета директоров"


def test_is_director_position_title() -> None:
    assert is_director_position_title("Директор")
    assert is_director_position_title("Заместитель технического директора")
    assert is_director_position_title("Председатель совета директоров")
    assert not is_director_position_title("Начальник отдела")
    assert not is_director_position_title("Помощник Председателя совета директоров")


def test_collect_attendees_uses_positions_report(tmp_path: Path, monkeypatch) -> None:
    report_path = tmp_path / "enterprise_positions_report_all.txt"
    report_path.write_text(SAMPLE_REPORT, encoding="utf-8-sig")
    monkeypatch.setenv("MEETING_ENTERPRISE_POSITIONS_REPORT", str(report_path))

    detail = {
        "application": {
            "initiator": {"full_name": "A"},
            "manager": {"full_name": "B"},
            "participants": [
                {"full_name": "Амураль Игорь Борисович"},
                {"full_name": "Яковлева Светлана Викторовна"},
                {"full_name": "Комарькова Анастасия Эдуардовна"},
            ],
        }
    }

    attendees = collect_attendees_from_detail(detail)

    assert attendees == [
        ("A", "initiator"),
        ("B", "manager"),
        ("Амураль Игорь Борисович", "director"),
        ("Яковлева Светлана Викторовна", "participant"),
        ("Комарькова Анастасия Эдуардовна", "participant"),
    ]


def test_real_report_finds_technical_director() -> None:
    report_path = Path(__file__).resolve().parents[2] / "tools" / "onec" / "enterprise_positions_report_all.txt"
    if not report_path.is_file():
        return

    positions = lookup_positions_by_fio("Амураль Игорь Борисович", report_path=report_path)
    assert "Председатель совета директоров" in positions
    assert is_director_by_fio("Амураль Игорь Борисович", report_path=report_path)
    assert resolve_priority_role("participant", {"full_name": "Амураль Игорь Борисович"}) == PRIORITY_DIRECTOR
    assert resolve_priority_role("participant", {"full_name": "Яковлева Светлана Викторовна"}) == PRIORITY_PARTICIPANT
