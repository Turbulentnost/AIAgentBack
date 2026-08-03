"""Tests for marking stats deduplication."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.services.marking_service import MarkingService


class _FakeLabel:
    def __init__(
        self,
        *,
        document_id: uuid.UUID,
        page_level: list,
        document_level: list | None = None,
        updated_at: datetime | None = None,
        is_rework: bool = False,
    ) -> None:
        self.id = uuid.uuid4()
        self.document_id = document_id
        self.page_level = page_level
        self.document_level = document_level or []
        self.updated_at = updated_at or datetime.now(timezone.utc)
        self.is_rework = is_rework
        self.check_run_id = None


class _FakeSession:
    pass


def test_compute_stats_counts_page_level_only_not_doubled() -> None:
    doc_id = uuid.uuid4()
    label = _FakeLabel(
        document_id=doc_id,
        page_level=[
            {
                "page": 1,
                "gost_findings": [
                    {"gost_key": "2.104", "severity": "error", "pages": [1], "note": ""}
                ],
                "note": "",
            }
        ],
        document_level=[
            {"gost_key": "2.104", "severity": "error", "pages": [1], "note": ""}
        ],
    )

    svc = MarkingService(_FakeSession())  # type: ignore[arg-type]

    async def _list_labels(*, limit: int = 100):
        return [label]

    async def _empty_scalars(_query):
        class _Result:
            def all(self):
                return []

        return _Result()

    svc.list_labels = _list_labels  # type: ignore[method-assign]
    svc._db.scalars = _empty_scalars  # type: ignore[method-assign]

    import asyncio

    items = asyncio.run(svc.compute_stats())
    row = next(i for i in items if i["gost_key"] == "2.104")
    assert row["error_count"] == 1
    assert row["total"] == 1
    assert row["after_ai_total"] == 0


def test_label_is_after_ai_check_by_filename_and_time() -> None:
    doc_id = uuid.uuid4()
    check_time = datetime(2026, 7, 17, 6, 0, tzinfo=timezone.utc)
    mark_time = datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc)

    class _Doc:
        source_filename = "sample.pdf"

    class _Run:
        created_at = check_time

    label = _FakeLabel(
        document_id=doc_id,
        page_level=[],
        updated_at=mark_time,
    )

    assert (
        MarkingService._label_is_after_ai_check(
            label,  # type: ignore[arg-type]
            _Doc(),  # type: ignore[arg-type]
            runs_by_filename={"sample.pdf": [_Run()]},  # type: ignore[arg-type]
        )
        is True
    )


def test_label_is_after_ai_check_when_linked_to_run() -> None:
    label = _FakeLabel(document_id=uuid.uuid4(), page_level=[])
    label.check_run_id = uuid.uuid4()

    assert (
        MarkingService._label_is_after_ai_check(
            label,  # type: ignore[arg-type]
            None,
            runs_by_filename={},
        )
        is True
    )
