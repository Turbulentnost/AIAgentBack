"""Классификация ролей: только 4 типа, ТАМОЖНЯ → shipment_schedule."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.document_analysis_agent.excel_service import (
    ROLE_OTHER,
    ROLE_SHIPMENT_SCHEDULE,
    UPLOAD_FILE_ROLES,
    UploadedWorkbook,
    _build_workbook_previews,
    _classify_preview_locally,
    _normalize_lm_role,
    classify_aveon_excel_files,
)

_TEST_DIR = Path(r"c:\Users\uaa\Desktop\test объединение")
_TAM_PATH = _TEST_DIR / "ТАМОЖНЯ.xlsx"
_GRAFIK_PATH = _TEST_DIR / "ГРАФИК ОТГРУЗОК (расширенный).xlsx"


@pytest.mark.skipif(not _TAM_PATH.is_file(), reason="test file ТАМОЖНЯ.xlsx not on disk")
def test_tamozhnya_preview_has_real_rows() -> None:
    previews = _build_workbook_previews(
        [UploadedWorkbook(filename=_TAM_PATH.name, content=_TAM_PATH.read_bytes())]
    )
    preview = previews[0]
    assert preview["sheets"], "expected sheet previews"
    first = preview["sheets"][0]
    assert (first.get("max_row") or 0) > 5, "read_only fallback should expose real row count"


@pytest.mark.skipif(not _TAM_PATH.is_file(), reason="test file ТАМОЖНЯ.xlsx not on disk")
def test_tamozhnya_local_fallback_is_shipment() -> None:
    previews = _build_workbook_previews(
        [UploadedWorkbook(filename=_TAM_PATH.name, content=_TAM_PATH.read_bytes())]
    )
    role = _classify_preview_locally(previews[0])
    assert role == ROLE_SHIPMENT_SCHEDULE
    assert role in UPLOAD_FILE_ROLES


@pytest.mark.skipif(
    not (_TAM_PATH.is_file() and _GRAFIK_PATH.is_file()),
    reason="test merge files not on disk",
)
@pytest.mark.asyncio
async def test_grafik_and_tamozhnya_roles_together() -> None:
    workbooks = [
        UploadedWorkbook(filename=_GRAFIK_PATH.name, content=_GRAFIK_PATH.read_bytes()),
        UploadedWorkbook(filename=_TAM_PATH.name, content=_TAM_PATH.read_bytes()),
    ]
    roles, _source = await classify_aveon_excel_files(workbooks)
    assert roles[_GRAFIK_PATH.name] == ROLE_SHIPMENT_SCHEDULE
    assert roles[_TAM_PATH.name] == ROLE_SHIPMENT_SCHEDULE
    assert all(r in UPLOAD_FILE_ROLES for r in roles.values())


def test_lm_role_tamozhnya_maps_to_shipment() -> None:
    assert _normalize_lm_role("таможня") == ROLE_SHIPMENT_SCHEDULE
    assert _normalize_lm_role("customs_itc") == ROLE_SHIPMENT_SCHEDULE
    assert _normalize_lm_role("specification") == ROLE_OTHER


def test_upload_roles_count_is_four() -> None:
    assert len(UPLOAD_FILE_ROLES) == 4
