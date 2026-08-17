import io
from datetime import date

import pytest
from openpyxl import Workbook

from app.agents.document_analysis_agent.temp_schedule_merge import (
    COUNTRY_META_KEY,
    SUPPLIER_COUNTRY_RUSSIA,
    NameDict,
    _META_HEADERS,
    _build_name_dict,
    _enrich_name,
    _find_name_col,
    _finalize_schedule_layout,
    _parse_schedule_layout,
    merge_schedule_files,
)


def test_find_name_col_prefers_nomenclature_over_product() -> None:
    header = ["Изделие", "Номенклатура", "Страна"]
    assert _find_name_col(header) == 1


def test_schedule_layout_keeps_source_name_without_itc_enrichment() -> None:
    nd = NameDict(
        entries=[("P-100", "Корпус ABC", "P-100 Корпус ABC")],
        full_by_model={"корпус abc": "P-100 Корпус ABC"},
    )
    assert _enrich_name("Корпус ABC", nd) == "P-100 Корпус ABC"
    assert _enrich_name("Корпус ABC", NameDict()) == "Корпус ABC"


def _realistic_russia_workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "График"
    ws.append([*_META_HEADERS, date(2026, 8, 12), date(2026, 8, 20)])
    ws.append(
        [
            "Винт M3x8 оцинкованный",
            "FPV-перехватчик Сокол",
            SUPPLIER_COUNTRY_RUSSIA,
            120,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            120,
            "",
        ]
    )
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


@pytest.mark.asyncio
async def test_merge_schedule_preserves_russia_nomenclature_without_google() -> None:
    result = await merge_schedule_files(
        [("russia.xlsx", _realistic_russia_workbook())],
        include_google_sheets=False,
        include_merged_inputs=True,
    )

    assert result["ok"] is True
    preview = result["preview_values"]
    assert preview[1][0] == "Винт M3x8 оцинкованный"
    assert preview[1][1] == "FPV-перехватчик Сокол"
    assert preview[1][2] == SUPPLIER_COUNTRY_RUSSIA
    assert result["stats"]["lm_used"] is False
    assert result["stats"]["elapsed_ms"] < 15_000


@pytest.mark.asyncio
async def test_merge_does_not_call_lm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("LM should not run during schedule merge")

    monkeypatch.setattr(
        "app.agents.document_analysis_agent.temp_schedule_merge._lm_detect_layout",
        fail,
    )
    monkeypatch.setattr(
        "app.agents.document_analysis_agent.temp_schedule_merge._lm_find_duplicates",
        fail,
    )
    monkeypatch.setattr(
        "app.agents.document_analysis_agent.temp_schedule_merge._lm_match_chunk",
        fail,
    )
    result = await merge_schedule_files(
        [("russia.xlsx", _realistic_russia_workbook())],
        include_google_sheets=False,
        include_merged_inputs=True,
    )
    assert result["ok"] is True
    assert result["preview_values"]
    assert result["stats"]["lm_used"] is False


def test_finalize_schedule_layout_pins_standard_columns() -> None:
    rows = [
        tuple([*_META_HEADERS, date(2026, 8, 12)]),
        tuple(["Имя", "Изделие X", SUPPLIER_COUNTRY_RUSSIA, 1] + [""] * 9 + [1]),
    ]
    from app.agents.document_analysis_agent.temp_schedule_merge import SheetLayout

    layout = SheetLayout(kind="schedule", header_row=0, data_start_row=1, name_col=1)
    layout = _finalize_schedule_layout(rows, layout)
    assert layout.name_col == 0
    assert layout.meta_cols["Изделие"] == 1
    assert layout.meta_cols[COUNTRY_META_KEY] == 2
