"""Tests for marking document lookup by filename."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.services.marking_service import MarkingService


class _FakeDoc:
    def __init__(self, name: str, *, updated_at: datetime | None = None) -> None:
        self.id = uuid.uuid4()
        self.source_filename = name
        self.updated_at = updated_at or datetime.now(timezone.utc)
        self.pages = [{"page": 1}]


class _FakeLabel:
    def __init__(self, doc_id: uuid.UUID, *, updated_at: datetime) -> None:
        self.id = uuid.uuid4()
        self.document_id = doc_id
        self.page_level = [{"page": 1, "gost_findings": [], "note": ""}]
        self.updated_at = updated_at


class _FakeSession:
    pass


def test_find_latest_document_by_filename_prefers_latest_label() -> None:
    older = datetime(2026, 7, 17, 6, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc)
    doc_old = _FakeDoc("Sample.PDF", updated_at=older)
    doc_new = _FakeDoc("sample.pdf", updated_at=newer)
    label = _FakeLabel(doc_old.id, updated_at=newer)

    svc = MarkingService(_FakeSession())  # type: ignore[arg-type]

    async def _scalars(_query):
        class _Result:
            def all(self_inner):
                return [doc_new, doc_old]

        return _Result()

    async def _latest(doc_id: uuid.UUID):
        return label if doc_id == doc_old.id else None

    svc._db.scalars = _scalars  # type: ignore[method-assign]
    svc.get_latest_label_for_document = _latest  # type: ignore[method-assign]

    import asyncio

    found = asyncio.run(svc.find_latest_document_by_filename("sample.pdf"))
    assert found is doc_old
