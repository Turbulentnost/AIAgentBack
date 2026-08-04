"""Tests for iterative 1C oracle BGE training."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent_pochta.routing.bge_department import BgeDepartmentPrediction
from agent_pochta.services.email_corpus_resolver import ResolvedEmailContent


@pytest.fixture
def sample_docs() -> list[dict]:
    return [
        {
            "Number": "АЛ00-000001",
            "Ref_Key": "11111111-1111-1111-1111-111111111111",
            "Кому": "00-000065",
            "ТемаСлужебнойЗаписки": "Тема 1",
        },
        {
            "Number": "АЛ00-000002",
            "Ref_Key": "22222222-2222-2222-2222-222222222222",
            "Кому": "00-000128",
            "ТемаСлужебнойЗаписки": "Тема 2",
        },
    ]


def test_run_iteration_counts_correct_and_upserts_wrong(monkeypatch: pytest.MonkeyPatch, sample_docs) -> None:
    from scripts import train_bge_iterative_1c_oracle as mod

    settings = MagicMock()
    settings.email_rag_min_chars = 10
    settings.bge_dept_min_score = 0.8

    def fake_resolve(doc, **kwargs):
        number = doc.get("Number")
        if number == "АЛ00-000001":
            text = "Длинный текст письма номер один для embedding"
        else:
            text = "Длинный текст письма номер два для embedding"
        return ResolvedEmailContent(
            embed_text=text,
            recipient="sales@turbo-don.ru",
            sender_email="a@b.ru",
            subject=str(doc.get("ТемаСлужебнойЗаписки") or ""),
            row=None,
            message_id=None,
            resolution_source="test",
            meta={},
        )

    predictions = {
        "Длинный текст письма номер один для embedding": BgeDepartmentPrediction(
            ok=True, dept_id="00-000065", dept_name="МТО", score=0.91
        ),
        "Длинный текст письма номер два для embedding": BgeDepartmentPrediction(
            ok=True, dept_id="00-000065", dept_name="МТО", score=0.70
        ),
    }

    monkeypatch.setattr(mod, "resolve_email_for_doc", fake_resolve)
    monkeypatch.setattr(
        mod,
        "predict_department_bge",
        lambda text, recipient, settings=None: predictions[text],
    )
    upsert_calls: list[dict] = []
    monkeypatch.setattr(
        mod,
        "upsert_correction_from_1c_oracle",
        lambda **kwargs: upsert_calls.append(kwargs) or {"ok": True, "indexed": 1},
    )

    summary, rows = mod.run_iteration(
        sample_docs,
        settings=settings,
        session=MagicMock(),
        code_by_guid={},
        name_by_code={"00-000065": "МТО", "00-000128": "Продажи"},
        reextract=False,
        upsert_on_miss=True,
        min_score=0.8,
    )

    assert summary["evaluated"] == 2
    assert summary["correct"] == 1
    assert summary["wrong"] == 1
    assert summary["accuracy"] == 0.5
    assert len(upsert_calls) == 1
    assert upsert_calls[0]["correct_dept_id"] == "00-000128"
    assert upsert_calls[0]["wrong_dept_id"] == "00-000065"


def test_train_until_target_stops_at_accuracy(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import train_bge_iterative_1c_oracle as mod

    monkeypatch.setattr(mod, "purge_index", lambda **kwargs: {"deleted": 0})
    monkeypatch.setattr(mod, "fetch_agent_incoming_docs", lambda since, **kwargs: [{"Number": "X", "Кому": "00-000065"}])
    monkeypatch.setattr(mod, "export_excel", lambda rows, path: None)
    monkeypatch.setattr(
        mod,
        "get_session_factory",
        lambda: MagicMock(**{"return_value.__enter__.return_value": MagicMock(), "return_value.__exit__.return_value": None}),
    )

    calls = {"n": 0}

    def fake_iteration(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"evaluated": 10, "correct": 8, "wrong": 2, "skipped": 0, "upserted": 2, "direct_routes": 7, "accuracy": 0.8, "min_score": 0.8}, []
        return {"evaluated": 10, "correct": 9, "wrong": 1, "skipped": 0, "upserted": 1, "direct_routes": 8, "accuracy": 0.9, "min_score": 0.8}, []

    monkeypatch.setattr(mod, "run_iteration", fake_iteration)
    monkeypatch.setattr(mod, "load_guid_maps", lambda settings=None: ({}, {}))

    result = mod.train_until_target(
        since="2026-07-20",
        target=0.9,
        max_iterations=5,
        limit=10,
        dry_run=True,
    )
    assert result["meets_target"] is True
    assert result["iterations_run"] == 2


def test_dry_run_does_not_upsert(monkeypatch: pytest.MonkeyPatch, sample_docs) -> None:
    from scripts import train_bge_iterative_1c_oracle as mod

    settings = MagicMock()
    settings.email_rag_min_chars = 10
    settings.bge_dept_min_score = 0.8

    monkeypatch.setattr(
        mod,
        "resolve_email_for_doc",
        lambda doc, **kwargs: ResolvedEmailContent(
            embed_text="Длинный текст для embedding проверки",
            recipient="sales@turbo-don.ru",
            sender_email="a@b.ru",
            subject="s",
            row=None,
            message_id=None,
            resolution_source="test",
            meta={},
        ),
    )
    monkeypatch.setattr(
        mod,
        "predict_department_bge",
        lambda text, recipient, settings=None: BgeDepartmentPrediction(
            ok=True, dept_id="00-000066", dept_name="X", score=0.5
        ),
    )
    upsert_mock = MagicMock(return_value={"ok": True, "indexed": 1})
    monkeypatch.setattr(mod, "upsert_correction_from_1c_oracle", upsert_mock)

    mod.run_iteration(
        sample_docs[:1],
        settings=settings,
        session=MagicMock(),
        code_by_guid={},
        name_by_code={"00-000065": "МТО"},
        reextract=False,
        upsert_on_miss=False,
        min_score=0.8,
    )
    upsert_mock.assert_not_called()
