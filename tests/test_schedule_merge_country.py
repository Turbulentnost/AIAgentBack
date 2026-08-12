import base64
import io
from datetime import date

import pytest
from openpyxl import Workbook

from app.agents.document_analysis_agent.temp_schedule_merge import (
    COUNTRY_META_KEY,
    SUPPLIER_COUNTRY_CHINA,
    SUPPLIER_COUNTRY_RUSSIA,
    _META_HEADERS,
    NomRow,
    _apply_supplier_country,
    _country_for_sources,
    _merge_supplier_country,
    apply_manager_date_change_to_schedule,
    merge_schedule_files,
)


def test_country_for_sources_grafik_only() -> None:
    assert _country_for_sources(["график:Лист1"]) == SUPPLIER_COUNTRY_RUSSIA


def test_country_for_sources_itc_only() -> None:
    assert _country_for_sources(["итц:спецификация"]) == SUPPLIER_COUNTRY_CHINA


def test_country_for_sources_mixed_prefers_china() -> None:
    assert _country_for_sources(["график:Лист1", "итц:партия"]) == SUPPLIER_COUNTRY_CHINA


def test_apply_supplier_country_writes_meta() -> None:
    row = NomRow(name="Деталь", sources=["график:График"])
    _apply_supplier_country(row)
    assert row.meta[COUNTRY_META_KEY] == SUPPLIER_COUNTRY_RUSSIA


def test_merge_supplier_country_prefers_china_on_conflict() -> None:
    assert (
        _merge_supplier_country(SUPPLIER_COUNTRY_RUSSIA, SUPPLIER_COUNTRY_CHINA)
        == SUPPLIER_COUNTRY_CHINA
    )


def _schedule_workbook(country: str = SUPPLIER_COUNTRY_RUSSIA) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "График"
    ws.append([*_META_HEADERS, date(2026, 8, 12), date(2026, 8, 20)])
    ws.append(
        [
            "Деталь А",
            "Спецификация Тест",
            country,
            10,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            10,
            "",
        ]
    )
    for index in range(2, 21):
        ws.append(
            [
                f"Деталь {index}",
                "Спецификация Тест",
                country,
                index,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                index,
                "",
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


@pytest.mark.asyncio
async def test_merge_schedule_files_can_skip_google_sheets() -> None:
    result = await merge_schedule_files(
        [("russia.xlsx", _schedule_workbook())],
        include_google_sheets=False,
        include_merged_inputs=True,
    )

    assert result["ok"] is True
    assert result["stats"]["google_sheets"]["source"] == "disabled"
    assert result["file_base64"]


@pytest.mark.asyncio
async def test_merge_schedule_files_keeps_merged_schedule_when_explicitly_allowed() -> None:
    first = await merge_schedule_files(
        [("russia.xlsx", _schedule_workbook())],
        include_google_sheets=False,
        include_merged_inputs=True,
    )
    assert first["ok"] is True

    second = await merge_schedule_files(
        [("merged_schedule.xlsx", base64.b64decode(first["file_base64"]))],
        include_google_sheets=False,
        include_merged_inputs=True,
    )

    assert second["ok"] is True
    assert second["stats"]["grafik_rows_raw"] == 20
    assert second["stats"]["nomenclature_total"] == 20
    assert second["preview_values"][1][1] == "Спецификация Тест"


@pytest.mark.asyncio
async def test_apply_manager_date_change_returns_country() -> None:
    result = await apply_manager_date_change_to_schedule(
        raw=_schedule_workbook(SUPPLIER_COUNTRY_CHINA),
        task_type="Поставка",
        problem="По Деталь А плановая дата 12.08.2026, количество 10 шт.",
        solution="Уточнить поставку",
        nomenclature="Деталь А",
        manager_result="Новая дата 20.08.2026, 10 шт.",
    )

    assert result["ok"] is True
    assert result["applied"] is True
    assert result["country"] == SUPPLIER_COUNTRY_CHINA
    assert result["matched_row"] == 2
    assert result["changed_cells"]
