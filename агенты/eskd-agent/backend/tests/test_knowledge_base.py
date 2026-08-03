"""Tests for knowledge base aggregation."""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.knowledge_base_service import (
    KnowledgeBaseService,
    _KbRow,
    _add_verifier,
    _count_marking_findings,
    _entry_key,
    _format_person_short,
    _kb_display_name,
    _merge_entry_key,
)


def test_entry_key_prefers_sha256() -> None:
    assert _entry_key(sha256="abc", filename="a.pdf") == "sha:abc"


def test_kb_display_name_prefers_designation() -> None:
    assert _kb_display_name(designation="UFG-800-16.02.00.000", filename="doc.pdf") == "UFG-800-16.02.00.000"
    assert _kb_display_name(designation=None, filename="doc.pdf") == "doc.pdf"
    assert _kb_display_name(designation="  ", filename=None) == "Без имени"


def test_kb_row_default_sort_ts() -> None:
    row = _KbRow(key="name:test.pdf", display_name="test.pdf")
    assert row.sort_ts == datetime.min


def test_merge_entry_key_uses_filename_sha() -> None:
    key = _merge_entry_key(
        sha256=None,
        filename="Sample.PDF",
        filename_to_sha={"sample.pdf": "abc123"},
    )
    assert key == "sha:abc123"


def test_format_person_short() -> None:
    assert _format_person_short("Арсуноев Михаил") == "Арсуноев М."


def test_verifiers_in_payload() -> None:
    svc = KnowledgeBaseService(None)  # type: ignore[arg-type]
    row = _KbRow(
        key="sha:111",
        display_name="checked.pdf",
        checked=True,
        verifiers=["Арсуноев М."],
        sort_ts=datetime.now(timezone.utc),
    )
    payload = svc._to_dict(row)
    assert payload["verifiers"] == ["Арсуноев М."]
    assert payload["verifiers_count"] == 1


def test_marking_without_human_verify_is_unchecked() -> None:
    svc = KnowledgeBaseService(None)  # type: ignore[arg-type]
    marked = _KbRow(
        key="name:only-mark.pdf",
        display_name="only-mark.pdf",
        has_marking=True,
        checked=False,
        marked_pages_count=2,
        marking_updated_at=datetime.now(timezone.utc),
        sort_ts=datetime.now(timezone.utc),
    )
    payload = svc._to_dict(marked)
    assert payload["checked"] is False
    assert payload["marking_updated_at"] is not None


def test_ai_only_entry_is_unchecked() -> None:
    svc = KnowledgeBaseService(None)  # type: ignore[arg-type]
    ai_only = _KbRow(
        key="sha:abc",
        display_name="ai.pdf",
        checked=False,
        has_ai_check=True,
        check_count=1,
        sort_ts=datetime.now(timezone.utc),
    )
    assert svc._to_dict(ai_only)["checked"] is False
    assert svc._to_dict(ai_only)["has_ai_check"] is True


def test_add_verifier_deduplicates() -> None:
    row = _KbRow(key="sha:1", display_name="a.pdf")
    _add_verifier(row, "Арсуноев Михаил")
    _add_verifier(row, "Арсуноев Михаил")
    assert row.verifiers == ["Арсуноев М."]


def test_resolve_delete_targets_by_name() -> None:
    class _Doc:
        def __init__(self, name: str) -> None:
            self.source_filename = name

    class _Run:
        def __init__(self, name: str, sha: str | None = None) -> None:
            self.original_filename = name
            self.file_sha256 = sha

    docs = [_Doc("Sample.PDF"), _Doc("Other.pdf")]
    runs = [_Run("Sample.PDF", "abc"), _Run("Other.pdf", "def")]

    del_docs, del_runs = KnowledgeBaseService._resolve_delete_targets(
        "name:sample.pdf",
        marking_docs=docs,  # type: ignore[arg-type]
        check_runs=runs,  # type: ignore[arg-type]
    )
    assert len(del_docs) == 1
    assert del_docs[0].source_filename == "Sample.PDF"
    assert len(del_runs) == 1
    assert del_runs[0].file_sha256 == "abc"


def test_resolve_delete_targets_by_sha() -> None:
    class _Doc:
        def __init__(self, name: str) -> None:
            self.source_filename = name

    class _Run:
        def __init__(self, name: str, sha: str) -> None:
            self.original_filename = name
            self.file_sha256 = sha

    docs = [_Doc("Sample.PDF")]
    runs = [_Run("Sample.PDF", "abc123")]

    del_docs, del_runs = KnowledgeBaseService._resolve_delete_targets(
        "sha:abc123",
        marking_docs=docs,  # type: ignore[arg-type]
        check_runs=runs,  # type: ignore[arg-type]
    )
    assert len(del_docs) == 1
    assert len(del_runs) == 1


def test_count_marking_findings_ignores_ok_and_document_level() -> None:
    page_level = [
        {
            "page": 1,
            "gost_findings": [
                {"gost_key": "2.104", "severity": "error"},
                {"gost_key": "2.105", "severity": "warning"},
                {"gost_key": "2.301", "severity": "ok"},
            ],
        },
        {
            "page": 2,
            "gost_findings": [{"gost_key": "2.201", "severity": "error"}],
        },
    ]
    assert _count_marking_findings(page_level) == (2, 1)


def test_marking_counts_in_payload() -> None:
    svc = KnowledgeBaseService(None)  # type: ignore[arg-type]
    row = _KbRow(
        key="name:marked.pdf",
        display_name="marked.pdf",
        has_marking=True,
        marking_errors_count=2,
        marking_warnings_count=1,
        total_errors=28,
        total_warnings=14,
        sort_ts=datetime.now(timezone.utc),
    )
    payload = svc._to_dict(row)
    assert payload["marking_errors_count"] == 2
    assert payload["marking_warnings_count"] == 1
    assert payload["total_errors"] == 28


def test_merge_checked_and_unchecked_rows() -> None:
    svc = KnowledgeBaseService(None)  # type: ignore[arg-type]
    checked = _KbRow(
        key="sha:111",
        display_name="checked.pdf",
        checked=True,
        check_count=1,
        sort_ts=datetime.now(timezone.utc),
    )
    unchecked = _KbRow(
        key="name:unknown.pdf",
        display_name="unknown.pdf",
        checked=False,
        sort_ts=datetime.now(timezone.utc),
    )
    assert svc._to_dict(checked)["checked"] is True
    assert svc._to_dict(unchecked)["checked"] is False
