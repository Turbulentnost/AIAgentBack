from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_open_marking_from_check_run_reuses_existing_document() -> None:
    run_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    fake_doc = type(
        "Doc",
        (),
        {
            "id": doc_id,
            "designation": None,
            "source_filename": "sample.pdf",
            "pages": [{"page": 1, "preview_path": f"{doc_id}/p01.jpg", "width": 100, "height": 100}],
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )()

    fake_run = type(
        "Run",
        (),
        {
            "id": run_id,
            "original_filename": "sample.pdf",
            "file_sha256": "abc",
            "designation": None,
        },
    )()

    with patch("app.api.marking.HistoryService") as history_cls, patch("app.api.marking.MarkingService") as marking_cls:
        history_cls.return_value.get_run = AsyncMock(return_value=fake_run)
        marking_cls.return_value.find_latest_document_by_filename = AsyncMock(return_value=fake_doc)
        marking_cls.return_value.get_latest_label_for_document = AsyncMock(return_value=None)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/eskd/marking/documents/open-from-check-run/{run_id}")

    assert resp.status_code == 200
    assert resp.json()["id"] == str(doc_id)
