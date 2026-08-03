"""Tests for check cache lookup by filename."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from app.api.check import lookup_check_cache


class _FakeLabel:
    page_level = [{"page": 1, "gost_findings": [], "note": ""}]


class _FakeDoc:
    id = uuid.uuid4()
    source_filename = "Sample.PDF"


def test_lookup_check_cache_found_marking() -> None:
    db = MagicMock()
    doc = _FakeDoc()
    label = _FakeLabel()

    marking = MagicMock()
    marking.find_latest_document_by_filename = AsyncMock(return_value=doc)
    marking.get_latest_label_for_document = AsyncMock(return_value=label)

    history = MagicMock()
    history.find_latest_by_filename = AsyncMock(return_value=None)

    kb = MagicMock()
    kb.list_entries = AsyncMock(
        return_value=(
            [
                {
                    "display_name": "Sample.PDF",
                    "checked": True,
                    "has_ai_check": False,
                }
            ],
            1,
            1,
            0,
        )
    )

    import app.api.check as check_api

    check_api.MarkingService = lambda _db: marking  # type: ignore[misc,assignment]
    check_api.HistoryService = lambda _db: history  # type: ignore[misc,assignment]
    check_api.KnowledgeBaseService = lambda _db: kb  # type: ignore[misc,assignment]

    import asyncio

    resp = asyncio.run(lookup_check_cache(filename="sample.pdf", checksum=None, db=db))
    assert resp.found is True
    assert resp.from_marking is True
    assert resp.checked_in_kb is True
    assert resp.message and "уже проверен" in resp.message
