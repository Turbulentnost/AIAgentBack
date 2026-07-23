"""Unit/API tests for OTK presentation CRUD + sample_rule + 1C stub."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.agents.quality_engineer_agent.otk_schemas import (
    OtkPresentationUpdate,
    OtkShipmentLineCreate,
    OtkShipmentLineUpdate,
)
from app.agents.quality_engineer_agent.otk_service import (
    OtkPresentationService,
    compute_line_sample_rule,
)
from app.agents.quality_engineer_agent.otk_store import reset_otk_store_for_tests
from app.api.v1.endpoints import otk as otk_endpoints


@pytest.fixture()
def otk_service(tmp_path: Path) -> OtkPresentationService:
    store = reset_otk_store_for_tests(tmp_path / "otk_presentations.json")
    return OtkPresentationService(store)


def test_sample_rule_depends_on_category(otk_service: OtkPresentationService) -> None:
    metal = compute_line_sample_rule(
        {"category": "metal", "qty_fact": 100, "nomenclature": "лист"}
    )
    fasteners = compute_line_sample_rule(
        {"category": "fasteners", "qty_fact": 100, "nomenclature": "болт"}
    )
    rated = compute_line_sample_rule(
        {
            "category": "cable",
            "qty_fact": 200,
            "supplier_quality_rating": 40,
            "nomenclature": "кабель",
        }
    )

    assert metal["sample_pct"] == 10.0
    assert metal["sample_size"] == 10
    assert metal["sample_basis"] == "10pct"

    assert fasteners["sample_basis"] == "per_package"
    assert fasteners["sample_size"] is None
    assert fasteners["sample_pct"] is None

    assert rated["sample_pct"] == 1.0
    assert rated["sample_basis"] == "1pct_rating"
    assert rated["sample_size"] == 2


def test_list_and_get_seeded(otk_service: OtkPresentationService) -> None:
    listing = otk_service.list_presentations()
    assert len(listing.items) == 30
    assert listing.pending_count == 25  # 5 done in seed
    assert listing.earliest_due_at is not None
    assert len(listing.workers) == 3
    assert all(item.project_code and item.project_name for item in listing.items)
    assert len({item.project_code for item in listing.items}) == 7
    assert len({item.purchase_order for item in listing.items}) == 30

    card = otk_service.get_presentation("pres-001")
    assert card is not None
    assert card.project_code == "PRJ-ТД-2026-01"
    assert card.project_name
    assert len(card.lines) == 5
    assert all(line.nomenclature for line in card.lines)
    assert {line.category for line in card.lines} >= {"metal", "pipes", "flanges", "gaskets"}
    assert card.lines[0].sample_rule is not None
    assert card.lines[0].sample_rule.category == "metal"
    assert card.lines[0].sample_rule.sample_pct == 10.0

    all_cards = [otk_service.get_presentation(item.id) for item in listing.items]
    assert all(c is not None for c in all_cards)
    total_lines = sum(len(c.lines) for c in all_cards if c is not None)
    assert total_lines >= 100
    categories = {line.category for c in all_cards if c for line in c.lines}
    assert categories >= {
        "metal",
        "pipes",
        "flanges",
        "gaskets",
        "cable",
        "fasteners",
        "electronics",
        "drawing_parts",
        "other",
    }


def test_update_line_category_recomputes_sample(
    otk_service: OtkPresentationService,
) -> None:
    before = otk_service.get_presentation("pres-001")
    assert before is not None
    line_id = before.lines[0].id
    assert before.lines[0].sample_rule is not None
    assert before.lines[0].sample_rule.sample_basis == "10pct"

    updated = otk_service.update_line(
        "pres-001",
        line_id,
        OtkShipmentLineUpdate(category="fasteners"),
    )
    assert updated is not None
    line = next(item for item in updated.lines if item.id == line_id)
    assert line.category == "fasteners"
    assert line.sample_rule is not None
    assert line.sample_rule.sample_basis == "per_package"
    assert line.sample_rule.sample_pct is None


def test_add_and_delete_line(otk_service: OtkPresentationService) -> None:
    before = otk_service.get_presentation("pres-003")
    assert before is not None
    base_count = len(before.lines)

    added = otk_service.add_line(
        "pres-003",
        OtkShipmentLineCreate(
            code="99",
            nomenclature="Тест",
            storage_unit="шт",
            qty_upd=50,
            qty_fact=50,
            category="metal",
        ),
    )
    assert added is not None
    assert len(added.lines) == base_count + 1
    new_id = added.lines[-1].id
    assert added.lines[-1].sample_rule is not None
    assert added.lines[-1].sample_rule.sample_size == 5

    deleted = otk_service.delete_line("pres-003", new_id)
    assert deleted is not None
    assert len(deleted.lines) == base_count


def test_patch_presentation_header(otk_service: OtkPresentationService) -> None:
    updated = otk_service.update_presentation(
        "pres-001",
        OtkPresentationUpdate(status="in_progress", storage_zone="Зона X"),
    )
    assert updated is not None
    assert updated.status == "in_progress"
    assert updated.storage_zone == "Зона X"


def test_write_to_1c_stub(otk_service: OtkPresentationService) -> None:
    result = otk_service.write_check_to_1c("pres-001")
    assert result is not None
    assert result.ok is True
    assert result.stub is True
    assert "1С" in result.message or "1C" in result.message.upper() or "заглушка" in result.message


@pytest.mark.asyncio
async def test_endpoint_requires_access(monkeypatch: pytest.MonkeyPatch) -> None:
    async def deny(*_args, **_kwargs):
        return False

    monkeypatch.setattr(otk_endpoints, "can_access_quality_engineer", deny)

    with pytest.raises(HTTPException) as exc:
        await otk_endpoints.list_otk_presentations(db=None, current_user=None)  # type: ignore[arg-type]
    assert exc.value.status_code == 403
