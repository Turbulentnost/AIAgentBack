"""Тесты детального графика формата «Отчёт» (П/ф · ОТК · Склад)."""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook

from app.agents.document_analysis_agent.excel_service import (
    ROLE_DETAILED_PRODUCTION_SCHEDULE,
    UploadedWorkbook,
    _classify_preview_locally,
    _enrich_merged_with_daily_demand,
    _extract_detailed_production_schedule,
    _infer_detailed_sheet_year_month,
    _parse_detailed_schedule_sheet,
    _preview_looks_like_detailed_production_schedule,
    _sheet_is_pf_stage_report,
    MergedNomenclatureRow,
)


def _pf_report_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Лист1"
    ws["B3"] = "Модель / изделие"
    ws["D2"] = "01.07.2026-17.07.2026"
    ws["D3"] = "план"
    ws["E3"] = "факт"
    ws["F2"] = datetime(2026, 7, 18)
    ws["F3"] = "план"
    ws["G3"] = "факт"
    ws["H2"] = "Итог недели"
    ws["H3"] = "План недели"
    ws["I3"] = "Факт недели"
    ws["J2"] = "План месяца"
    ws["K2"] = "Нарастающий"
    ws["K3"] = "план"
    ws["L3"] = "факт"
    ws["M3"] = "откл."

    ws["A4"] = 1
    ws["B4"] = "Сокол И"
    ws["C4"] = "П/ф"
    ws["D4"] = 6800
    ws["E4"] = 1995
    ws["G4"] = 500

    ws["C5"] = "ОТК"
    ws["D5"] = 6800
    ws["E5"] = 1995
    ws["G5"] = 9999  # не должно попасть в план/факт

    ws["C6"] = "Склад"
    ws["D6"] = 1

    ws["A7"] = 2
    ws["B7"] = "Сокол ИТ"
    ws["C7"] = "П/ф"
    ws["D7"] = 1700
    ws["F7"] = 100

    ws["A8"] = 5
    ws["C8"] = "П/ф"  # пустой слот без имени

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_pf_report_parser_uses_only_pf_and_distributes_range() -> None:
    from openpyxl import load_workbook

    content = _pf_report_bytes()
    wb = load_workbook(BytesIO(content), data_only=True)
    ws = wb.active
    assert _sheet_is_pf_stage_report(ws)
    assert _infer_detailed_sheet_year_month(ws, 2026) == (2026, 7)

    plans, plan_cells = _parse_detailed_schedule_sheet(ws, 2026, 7)
    assert {p.product for p in plans} == {"Сокол И", "Сокол ИТ"}
    assert plan_cells
    assert all(cell.plan_col >= 1 and cell.row >= 1 for cell in plan_cells)

    sok = next(p for p in plans if p.product == "Сокол И")
    plan_1_17 = sum(sok.daily_qty.get(date(2026, 7, d).isoformat(), 0.0) for d in range(1, 18))
    fact_1_17 = sum(sok.daily_fact.get(date(2026, 7, d).isoformat(), 0.0) for d in range(1, 18))
    assert plan_1_17 == 6800.0
    assert fact_1_17 == 1995.0
    assert sok.daily_qty.get("2026-07-01") == 400.0
    assert sok.daily_qty.get("2026-07-18") == 0.0
    assert sok.daily_fact.get("2026-07-18") == 500.0
    # ОТК 9999 не учитывается
    assert sok.daily_fact.get("2026-07-18") != 9999
    assert "2026-07-26" not in sok.daily_fact


def test_pf_report_extract_fills_plan_all_month_fact_only_existing() -> None:
    content = _pf_report_bytes()
    filename = "Отчет 07_2026_6148.xlsx"
    extract = _extract_detailed_production_schedule(
        [UploadedWorkbook(filename=filename, content=content)],
        {filename: ROLE_DETAILED_PRODUCTION_SCHEDULE},
        as_of=date(2026, 7, 24),
    )
    assert extract.year == 2026 and extract.month == 7
    assert len(extract.day_keys) == 31
    sok = next(p for p in extract.plans if p.product == "Сокол И")
    assert set(sok.daily_qty) == set(extract.day_keys)
    assert sok.daily_qty["2026-07-26"] == 0.0
    assert "2026-07-26" not in sok.daily_fact
    assert "2026-07-18" in sok.daily_fact
    assert set(sok.daily_fact).issubset(set(extract.day_keys))


def test_daily_demand_from_pf_plan_and_fact() -> None:
    content = _pf_report_bytes()
    filename = "Отчет 07_2026_6148.xlsx"
    extract = _extract_detailed_production_schedule(
        [UploadedWorkbook(filename=filename, content=content)],
        {filename: ROLE_DETAILED_PRODUCTION_SCHEDULE},
        as_of=date(2026, 7, 24),
    )
    row = MergedNomenclatureRow(
        nomenclature="Комплектующая А",
        products=["Сокол И полный комплект"],
        quantity=2.0,
        by_product={"Сокол И полный комплект": 2.0},
    )
    _enrich_merged_with_daily_demand([row], extract)
    sok = next(p for p in extract.plans if p.product == "Сокол И")
    # 400 изд. × 2 = 800 на 01.07 (план из диапазона)
    assert row.daily_demand["2026-07-01"] == 800.0
    # день без плана выпуска → 0
    assert row.daily_demand["2026-07-26"] == 0.0
    # план на 18.07 пустой → 0
    assert row.daily_demand["2026-07-18"] == 0.0
    # факт 500 на 18.07 × 2 = 1000
    assert row.daily_demand_fact["2026-07-18"] == 1000.0
    assert row.daily_demand_fact["2026-07-01"] == float(sok.daily_fact["2026-07-01"]) * 2
    assert row.daily_demand_fact["2026-07-26"] == 0.0


def test_preview_detects_otchet_filename() -> None:
    preview = {
        "filename": "Отчет 07_2026_6148.xlsx",
        "sheets": [
            {
                "name": "Лист1",
                "headers": ["Модель / изделие", "план", "факт", "П/ф"],
                "sample_rows": [["1", "Сокол И", "П/ф", 100]],
            }
        ],
    }
    assert _preview_looks_like_detailed_production_schedule(preview)
    assert _classify_preview_locally(preview) == ROLE_DETAILED_PRODUCTION_SCHEDULE
