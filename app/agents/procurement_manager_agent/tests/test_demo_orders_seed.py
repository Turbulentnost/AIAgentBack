"""Validate procurement-manager demo orders helper / disabled fixture."""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.procurement_manager_agent.demo_orders import (
    DATA_PATH,
    DEMO_CASE_1,
    DEMO_TAG,
    build_orders,
    write_fixture,
)


def test_build_orders_still_generates_varied_project_orders_for_tests() -> None:
    """Generator kept for unit tests / --force-demo; not loaded into the UI queue."""
    orders = build_orders()
    assert len(orders) == 30
    assert orders[0]["id"] == str(DEMO_CASE_1)

    projects = {item["project_code"] for item in orders}
    assert len(projects) >= 7

    position_counts = [len(item["positions"]) for item in orders]
    assert min(position_counts) >= 2
    assert max(position_counts) >= 5
    assert sum(position_counts) >= 80

    noms = {
        line["nomenclature_id"]
        for item in orders
        for line in item["positions"]
    }
    assert len(noms) >= 12
    assert all(item["current_agent_id"] == "purchase_manager_agent" for item in orders)


def test_write_fixture_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "demo_orders.json"
    orders = build_orders()
    path = write_fixture(orders, path=out)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["tag"] == DEMO_TAG
    assert payload["orders_count"] == 30
    assert payload["demo_case_id"] == str(DEMO_CASE_1)
    assert len(payload["orders"]) == 30


def test_checked_in_fixture_is_disabled_empty() -> None:
    """Production fixture must not ship demo orders into the manager queue."""
    if not DATA_PATH.exists():
        return
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    assert payload.get("orders_count", 0) == 0
    assert payload.get("orders") == []
    assert payload.get("disabled") is True
    assert payload.get("demo_case_id") == str(DEMO_CASE_1)
