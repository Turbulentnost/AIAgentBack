import io
from datetime import date

from openpyxl import Workbook

from app.agents.document_analysis_agent.workbook_preview import build_workbook_tab_preview


def _two_sheet_workbook() -> bytes:
    wb = Workbook()
    first = wb.active
    first.title = "График"
    first.append(["Номенклатура", "Кол-во", date(2026, 8, 17)])
    first.append(["Винт M3", 12, 4])
    second = wb.create_sheet("ТАМОЖНЯ")
    second.append(["Операция", "Основание"])
    second.append(["Выпуск", "ДТ-1"])
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def test_build_workbook_tab_preview_keeps_all_sheets() -> None:
    result = build_workbook_tab_preview("grafik.xlsx", _two_sheet_workbook())

    assert result["ok"] is True
    assert result["file_name"] == "grafik.xlsx"
    assert [sheet["name"] for sheet in result["sheets"]] == ["График", "ТАМОЖНЯ"]
    assert result["sheets"][0]["values"][0][0] == "Номенклатура"
    assert result["sheets"][0]["values"][1][0] == "Винт M3"
    assert result["sheets"][0]["values"][0][2] == "17.08.2026"
    assert result["sheets"][1]["values"][1][1] == "ДТ-1"
