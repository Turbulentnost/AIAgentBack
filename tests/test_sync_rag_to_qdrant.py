"""Тесты scripts/sync_rag_to_qdrant.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = ROOT / "scripts" / "sync_rag_to_qdrant.py"


def _import_sync_module():
    spec = importlib.util.spec_from_file_location("sync_rag_to_qdrant", SYNC_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sync_spam_learning_from_json_stub_backend(monkeypatch: pytest.MonkeyPatch):
    mod = _import_sync_module()
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()
    result = mod.sync_spam_learning_from_json()
    assert result["synced"] == 0
    assert result["reason"] == "stub_backend"


def test_sync_spam_learning_from_json_calls_resync(monkeypatch: pytest.MonkeyPatch):
    mod = _import_sync_module()
    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    from agent_pochta.config import reset_settings

    reset_settings()

    with patch.object(mod, "ensure_spam_learning_indexes") as ensure_mock:
        with patch.object(
            mod,
            "resync_spam_learning_to_qdrant",
            return_value={"synced": 3, "total": 3},
        ) as resync_mock:
            with patch.object(
                mod,
                "load_spam_learning",
                return_value={"entries": [{}, {}, {}]},
            ):
                with patch.object(mod, "collection_points", return_value=3):
                    result = mod.sync_spam_learning_from_json()

    ensure_mock.assert_called_once_with("http://qdrant:6333")
    resync_mock.assert_called_once()
    assert result["synced"] == 3
    assert result["json_entries"] == 3
    assert result["qdrant_points"] == 3


def test_apply_rag_department_keywords(monkeypatch: pytest.MonkeyPatch):
    mod = _import_sync_module()
    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    from agent_pochta.config import reset_settings

    reset_settings()

    with patch.object(
        mod,
        "load_department_keywords",
        return_value={"00-000001": ["сверка"], "00-000002": ["договор"]},
    ):
        with patch.object(
            mod,
            "append_department_keywords",
            side_effect=[
                {"updated": True, "keywords_added": 1},
                {"updated": False, "keywords_added": 0},
            ],
        ) as append_mock:
            result = mod.apply_rag_department_keywords()

    assert append_mock.call_count == 2
    assert result["departments_touched"] == 2
    assert result["departments_updated"] == 1
    assert result["keywords_added"] == 1


def test_apply_routing_correction_keywords(monkeypatch: pytest.MonkeyPatch):
    mod = _import_sync_module()
    monkeypatch.setenv("RAG_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    from agent_pochta.config import reset_settings

    reset_settings()

    corrections = {
        "entries": [
            {
                "department_id": "00-000002",
                "keywords": ["акт сверки"],
                "recipient": "buh@turbo-don.ru",
            }
        ]
    }
    with patch.object(mod, "load_corrections", return_value=corrections):
        with patch.object(
            mod,
            "enrich_department_in_qdrant",
            return_value={"updated": True, "keywords_added": 2},
        ) as enrich_mock:
            result = mod.apply_routing_correction_keywords()

    enrich_mock.assert_called_once()
    assert enrich_mock.call_args.args[0] == "00-000002"
    assert result["corrections"] == 1
    assert result["departments_updated"] == 1
    assert result["keywords_added"] == 2
