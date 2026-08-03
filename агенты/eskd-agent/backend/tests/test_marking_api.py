"""API tests for marking schemas and stats logic (no DB)."""

from __future__ import annotations

from app.gost.catalog import GOST_LINE_KEYS, gost_catalog
from app.schemas.marking import GostFinding, MarkingLabelCreate


def test_gost_catalog_has_eight_items() -> None:
    items = gost_catalog()
    assert len(items) == 8
    assert [i["key"] for i in items] == GOST_LINE_KEYS


def test_marking_label_create_schema() -> None:
    payload = MarkingLabelCreate(
        document_id="00000000-0000-0000-0000-000000000001",
        is_rework=True,
        document_level=[
            GostFinding(gost_key="2.104", severity="error", pages=[11, 12], note="нет подписи")
        ],
        page_level=[],
        problem_report="Проблемы на листах продолжения",
    )
    assert payload.is_rework is True
    assert payload.document_level[0].gost_key == "2.104"
