"""Local-first classification for Aveon Excel roles."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.document_analysis_agent.excel_service import (
    ROLE_DETAILED_PRODUCTION_SCHEDULE,
    ROLE_OTHER,
    ROLE_PRODUCTION_SCHEDULE,
    ROLE_SHIPMENT_SCHEDULE,
    ROLE_STOCK,
    UploadedWorkbook,
    _classify_preview_locally,
    _classify_workbooks_with_lm,
    _role_cache,
    _role_cache_put,
    _workbook_content_key,
    classify_aveon_excel_files,
)


def _preview(filename: str, *sheet_snippets: str) -> dict[str, Any]:
    rows = [[snippet] for snippet in sheet_snippets]
    return {
        "filename": filename,
        "sheets": [{"sheet": "Лист1", "max_row": 10, "max_column": 5, "sample_rows": rows}],
    }


def test_local_preview_stock_and_shipment():
    stock = _preview("остатки.xlsx", "Номенклатура", "Остаток на складе")
    ship = _preview("отгрузки.xlsx", "График отгрузок", "Номенклатура", "Логистика до МСК")
    assert _classify_preview_locally(stock) == ROLE_STOCK
    assert _classify_preview_locally(ship) == ROLE_SHIPMENT_SCHEDULE


def test_local_preview_detailed_and_monthly():
    detailed = _preview(
        "План по недельно.xlsx",
        "График выпуска готовой продукции",
        "01.07",
        "02.07",
        "03.07",
        "04.07",
        "05.07",
        "Сокол",
    )
    monthly = _preview(
        "график.xlsx",
        "График производства",
        "Наименования изделий",
        "Июль",
        "Август",
        "Заказ",
        "План",
    )
    assert _classify_preview_locally(detailed) == ROLE_DETAILED_PRODUCTION_SCHEDULE
    assert _classify_preview_locally(monthly) == ROLE_PRODUCTION_SCHEDULE


@pytest.mark.asyncio
async def test_classify_skips_lm_when_local_confident():
    previews = [
        _preview("С остатками.xlsx", "Номенклатура", "Остаток на 01.07"),
        _preview("ГРАФИК ОТГРУЗОК.xlsx", "График отгрузок", "Номенклатура", "Дата заказа"),
    ]
    with patch(
        "app.agents.document_analysis_agent.excel_service._try_lm_classify_workbooks",
        new_callable=AsyncMock,
    ) as lm:
        roles, source = await _classify_workbooks_with_lm(previews)
        lm.assert_not_called()
    assert source == "local_fast"
    assert roles["С остатками.xlsx"] == ROLE_STOCK
    assert roles["ГРАФИК ОТГРУЗОК.xlsx"] == ROLE_SHIPMENT_SCHEDULE


@pytest.mark.asyncio
async def test_classify_calls_lm_only_for_ambiguous():
    known = _preview("С остатками.xlsx", "Номенклатура", "Остаток на складе")
    ambiguous = _preview("mystery.xlsx", "Таблица", "Колонка А", "Колонка Б")
    assert _classify_preview_locally(ambiguous) == ROLE_OTHER

    with patch(
        "app.agents.document_analysis_agent.excel_service._try_lm_classify_workbooks",
        new_callable=AsyncMock,
        return_value={"mystery.xlsx": ROLE_PRODUCTION_SCHEDULE},
    ) as lm:
        roles, source = await _classify_workbooks_with_lm([known, ambiguous])
        lm.assert_called_once()
        sent = lm.await_args.args[0]
        assert len(sent) == 1
        assert sent[0]["filename"] == "mystery.xlsx"

    assert source == "local+lm"
    assert roles["С остатками.xlsx"] == ROLE_STOCK
    assert roles["mystery.xlsx"] == ROLE_PRODUCTION_SCHEDULE


@pytest.mark.asyncio
async def test_classify_uses_content_cache():
    _role_cache.clear()
    content = b"PK\x03\x04fake-xlsx-bytes-for-cache-test"
    key = _workbook_content_key(content)
    _role_cache_put(key, ROLE_STOCK)

    wb = UploadedWorkbook(filename="anything.xlsx", content=content)
    with patch(
        "app.agents.document_analysis_agent.excel_service._build_workbook_previews_async",
        new_callable=AsyncMock,
    ) as build:
        roles, source = await classify_aveon_excel_files([wb])
        build.assert_not_called()

    assert source == "cache"
    assert roles["anything.xlsx"] == ROLE_STOCK
