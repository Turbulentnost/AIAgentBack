"""Контракт analysis_result.json — то, что Авион примет от Cursor."""

from __future__ import annotations

import pytest

from app.agents.document_analysis_agent.cursor_cloud import (
    ANALYSIS_RESULT_SCHEMA_ID,
    AnalysisResultError,
    analysis_result_to_snapshot_blocks,
    has_dashboard_payload,
    parse_analysis_result,
)


def _empty_period(key: str, label: str) -> dict:
    tiles = {"all": 0, "green": 0, "yellow": 0, "red": 0}
    side = {"rows": [], "tiles": tiles}
    return {
        "key": key,
        "label": label,
        "days": [],
        "products": side,
        "nomenclatures": side,
    }


def _valid_coverage() -> dict:
    tiles = {"all": 1, "green": 1, "yellow": 0, "red": 0}
    return {
        "as_of": "2026-08-18",
        "schedule_month": "2026-08",
        "default_period": "day",
        "default_analysis_mode": "conditional",
        "periods": {
            "day": {
                "key": "day",
                "label": "За день",
                "days": ["2026-08-18"],
                "products": {
                    "rows": [
                        {
                            "name": "Изделие А",
                            "plan": 10,
                            "fact": 0,
                            "covered": 10,
                            "status": "green",
                            "assemblableQty": 10,
                        }
                    ],
                    "tiles": tiles,
                },
                "nomenclatures": {"rows": [], "tiles": {"all": 0, "green": 0, "yellow": 0, "red": 0}},
            },
            "week": _empty_period("week", "За неделю"),
            "month": _empty_period("month", "За месяц"),
        },
    }


def _valid_payload(**overrides) -> dict:
    payload = {
        "schema_id": ANALYSIS_RESULT_SCHEMA_ID,
        "schema_version": 1,
        "as_of": "2026-08-18",
        "roles": [
            {"filename": "plan.xlsx", "role": "production_schedule", "source": "upload"},
            {"filename": "stock.xlsx", "role": "stock", "source": "1c"},
        ],
        "logistics_risks": {
            "as_of": "2026-08-18",
            "stages": [
                {
                    "key": "msk_arrival",
                    "label": "Прибытие в МСК",
                    "items": [
                        {
                            "nomenclature": "Болт М8",
                            "supplier": "ООО Поставщик",
                            "quantity": 100,
                            "moscow_date": "2026-08-20",
                            "milestone_date": "2026-08-20",
                            "sheet": "Лист1",
                            "risk_level": "high",
                        }
                    ],
                }
            ],
        },
        "coverage_dashboard": _valid_coverage(),
        "task_dashboard": {
            "values": [["№", "Тип задания"], ["1", "Закупка"]],
            "row_priorities": [None, "urgent"],
            "row_kinds": ["header", "task"],
            "meta": {
                "as_of": "18.08.2026",
                "week_period": "17.08.2026 — 23.08.2026",
                "week_in_period": True,
                "task_count": 1,
                "urgent_count": 1,
                "today_count": 0,
                "week_count": 0,
            },
            "result_texts": {},
            "result_evals": {},
        },
        "errors": [],
        "warnings": [],
    }
    payload.update(overrides)
    return payload


def test_parse_valid_result_and_snapshot_blocks() -> None:
    result = parse_analysis_result(_valid_payload())
    assert has_dashboard_payload(result)
    blocks = analysis_result_to_snapshot_blocks(result)
    assert blocks["logistics_risks"]["stages"][0]["key"] == "msk_arrival"
    assert blocks["coverage_dashboard"]["periods"]["day"]["products"]["tiles"]["all"] == 1
    assert blocks["task_dashboard"]["row_kinds"][1] == "task"
    assert "user_id" not in blocks
    assert "saved_at" not in blocks


def test_failed_analysis_with_errors_is_valid() -> None:
    result = parse_analysis_result(
        {
            "schema_version": 1,
            "as_of": "2026-08-18",
            "logistics_risks": {"as_of": None, "stages": []},
            "errors": [{"code": "no_spec", "message": "Нет спецификации для изделия А"}],
        }
    )
    assert not has_dashboard_payload(result)
    assert result.errors[0].message.startswith("Нет спецификации")


def test_empty_payload_without_errors_rejected() -> None:
    with pytest.raises(AnalysisResultError, match="coverage_dashboard"):
        parse_analysis_result(
            {
                "schema_version": 1,
                "as_of": "2026-08-18",
                "logistics_risks": {"stages": []},
            }
        )


def test_wrong_schema_version_rejected() -> None:
    with pytest.raises(AnalysisResultError, match="schema_version"):
        parse_analysis_result(_valid_payload(schema_version=2))


def test_invalid_json_rejected() -> None:
    with pytest.raises(AnalysisResultError, match="не JSON"):
        parse_analysis_result("{")
