"""Тесты локального staging вложений ERP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_pochta.services.erp_attachment_staging import (
    cleanup_staged_attachment,
    read_staged_bytes,
    stage_attachment_bytes,
    write_roundtrip_report,
)


def test_stage_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("ODATA_ATTACH_STAGING_DIR", str(tmp_path))
    monkeypatch.setenv("ODATA_ATTACH_STAGING_ENABLED", "true")

    staged = stage_attachment_bytes(
        b"%PDF-1.4 test",
        "doc.pdf",
        document_ref_key="18516943-871f-11f1-984b-6cb31113810e",
        document_number="АЛ00-000762",
        message_id="imap-123",
    )
    assert staged.path.is_file()
    assert read_staged_bytes(staged.path) == b"%PDF-1.4 test"
    assert staged.manifest_path.is_file()
    manifest = json.loads(staged.manifest_path.read_text(encoding="utf-8"))
    assert manifest["document_number"] == "АЛ00-000762"

    report = write_roundtrip_report(
        staged,
        ref_key="abc",
        odata_bytes=b"%PDF-1.4 test",
        storage_kind="ВИнформационнойБазе",
    )
    assert report.is_file()
    assert json.loads(report.read_text(encoding="utf-8"))["bytes_match"] is True

    cleanup_staged_attachment(staged)
    assert not staged.path.is_file()
    assert not staged.manifest_path.is_file()
