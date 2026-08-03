"""Tests for check response built from saved marking."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.services.marking_check_cache import build_check_response_from_marking


class _FakeDoc:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.source_filename = "Drawing.PDF"
        self.designation = "UFG-800-16.02.00.000"
        self.pages = [{"page": 1}, {"page": 2}]


class _FakeLabel:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.problem_report = "Общее замечание"
        self.page_level = [
            {
                "page": 1,
                "note": "",
                "gost_findings": [
                    {
                        "gost_key": "2.104",
                        "severity": "error",
                        "note": "Нет подписи",
                    }
                ],
            },
            {
                "page": 2,
                "note": "Лист 2 без ошибок",
                "gost_findings": [],
            },
        ]


def test_build_check_response_from_marking_maps_findings() -> None:
    doc = _FakeDoc()
    label = _FakeLabel()

    payload = build_check_response_from_marking(
        filename="drawing.pdf",
        designation=None,
        doc=doc,  # type: ignore[arg-type]
        label=label,  # type: ignore[arg-type]
    )

    assert payload["status"] == "from_marking"
    assert payload["designation"] == doc.designation
    assert payload["total_items"] == 2
    assert payload["total_errors"] == 1
    assert payload["total_warnings"] == 1
    assert payload["total_infer_seconds"] == 0.0
    assert payload["marking_document_id"] == str(doc.id)
    assert payload["marking_label_id"] == str(label.id)

    page1 = payload["items"][0]
    assert page1["page"] == 1
    assert page1["status"] == "from_marking"
    assert page1["errors_count"] == 1
    assert page1["errors"][0]["code"] == "2.104"

    page2 = payload["items"][1]
    assert page2["page"] == 2
    assert page2["warnings_count"] == 1
    assert page2["warnings"][0]["code"] == "page_note"

    assert payload["gost_summary"]["errors"].get("2.104") == [1]
