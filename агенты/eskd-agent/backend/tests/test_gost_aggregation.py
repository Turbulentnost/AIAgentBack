"""Tests for GOST aggregation."""

from __future__ import annotations

from app.gost.aggregation import aggregate_gost_summary


def test_aggregate_empty_items_all_passed() -> None:
    summary = aggregate_gost_summary([])
    assert len(summary["passed"]) == 8
    assert summary["errors"] == {}
    assert summary["warnings"] == {}


def test_aggregate_error_on_page_maps_to_gost_line() -> None:
    items = [
        {
            "page": 11,
            "errors": [
                {
                    "code": "missing_signature",
                    "gost_reference": "ГОСТ Р 2.104-2023",
                    "element": "title_block",
                }
            ],
            "warnings": [],
            "checks": [],
        }
    ]
    summary = aggregate_gost_summary(items)
    assert "2.104" in summary["errors"]
    assert summary["errors"]["2.104"] == [11]
    assert "2.104" not in summary["passed"]


def test_aggregate_warning_foreign_overlay() -> None:
    items = [
        {
            "page": 3,
            "errors": [],
            "warnings": [],
            "overlays": [{"present": True}],
            "checks": [],
        }
    ]
    summary = aggregate_gost_summary(items)
    assert summary["warnings"].get("2.105") == [3]


def test_aggregate_package_errors_cross_page() -> None:
    payload = {
        "items": [],
        "package_errors": [
            {
                "code": "position_missing_in_bom",
                "severity": "error",
                "gost_reference": "ГОСТ 2.105",
                "pages": [2],
            }
        ],
    }
    from app.gost.aggregation import aggregate_from_check_response

    summary = aggregate_from_check_response(payload)
    assert summary["errors"].get("2.105") == [2]
    assert "2.105" not in summary["passed"]


def test_aggregate_sheet_sequence_is_internal_and_excluded_from_summary() -> None:
    payload = {
        "items": [{"page": i, "errors": [], "warnings": [], "checks": []} for i in range(1, 4)],
        "package_errors": [
            {
                "code": "sheet_sequence",
                "severity": "error",
                "gost_reference": "ГОСТ Р 2.104-2023",
                "message": "Нарушена последовательность номеров листов.",
            }
        ],
    }
    from app.gost.aggregation import aggregate_from_check_response

    summary = aggregate_from_check_response(payload)
    assert "2.104" not in summary["errors"]
    assert "2.104" in summary["passed"]


def test_aggregate_typo_in_designation() -> None:
    items = [
        {
            "page": 4,
            "errors": [
                {
                    "code": "typo_in_designation",
                    "gost_reference": "ГОСТ Р 2.201-2023",
                    "element": "references",
                }
            ],
            "warnings": [],
            "checks": [],
        }
    ]
    summary = aggregate_gost_summary(items)
    assert summary["errors"]["2.201"] == [4]
