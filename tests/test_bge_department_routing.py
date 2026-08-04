"""Tests for BGE department routing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent_pochta.routing.bge_department import predict_department_bge


@pytest.fixture
def settings():
    mock = MagicMock()
    mock.embedding_base_url = "http://embed.test/v1"
    mock.qdrant_url = "http://qdrant:6333"
    mock.bge_dept_top_k = 3
    return mock


def test_predict_empty_text(settings) -> None:
    result = predict_department_bge("", "sales@turbo-don.ru", settings=settings)
    assert result.ok is False
    assert result.reason == "empty_text"


def test_predict_no_embedding_url(settings) -> None:
    settings.embedding_base_url = ""
    result = predict_department_bge("hello", "sales@turbo-don.ru", settings=settings)
    assert result.ok is False
    assert result.reason == "no_embedding_url"


def test_predict_votes_top_department(settings) -> None:
    hits = [
        {"dept_correct_id": "00-000065", "dept_correct_name": "МТО", "score": 0.91},
        {"dept_correct_id": "00-000065", "dept_correct_name": "МТО", "score": 0.88},
        {"dept_correct_id": "00-000128", "dept_correct_name": "Продажи", "score": 0.80},
    ]

    with (
        patch("agent_pochta.routing.bge_department.embed_texts", return_value=[[0.1, 0.2]]),
        patch(
            "agent_pochta.routing.bge_department.search_department_corrections",
            return_value=hits,
        ) as search_mock,
    ):
        result = predict_department_bge(
            "Запрос на поставку",
            "uk_omto4@turbo-don.ru",
            settings=settings,
        )

    assert result.ok is True
    assert result.dept_id == "00-000065"
    assert result.score == pytest.approx(0.91)
    search_mock.assert_called_once()
    assert search_mock.call_args.kwargs["recipient"] == "uk_omto4@turbo-don.ru"


def test_predict_recipient_filter_and_allowed_departments(settings) -> None:
    hits = [
        {"dept_correct_id": "00-000065", "dept_correct_name": "МТО", "score": 0.95},
        {"dept_correct_id": "00-000128", "dept_correct_name": "Продажи", "score": 0.90},
    ]

    with (
        patch("agent_pochta.routing.bge_department.embed_texts", return_value=[[0.1]]),
        patch(
            "agent_pochta.routing.bge_department.search_department_corrections",
            return_value=hits,
        ),
    ):
        result = predict_department_bge(
            "Коммерческое предложение",
            "sales@turbo-don.ru",
            settings=settings,
            allowed_departments={"00-000128"},
        )

    assert result.ok is True
    assert result.dept_id == "00-000128"


def test_predict_respects_min_score_threshold(settings) -> None:
    hits = [{"dept_correct_id": "00-000065", "dept_correct_name": "МТО", "score": 0.72}]

    with (
        patch("agent_pochta.routing.bge_department.embed_texts", return_value=[[0.1]]),
        patch(
            "agent_pochta.routing.bge_department.search_department_corrections",
            return_value=hits,
        ),
    ):
        result = predict_department_bge("text", "sales@turbo-don.ru", settings=settings)

    assert result.ok is True
    assert result.score == pytest.approx(0.72)
    settings.bge_dept_min_score = 0.80
    assert result.score < settings.bge_dept_min_score
