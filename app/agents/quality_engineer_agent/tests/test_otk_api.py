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


def _first_card_with_category(
    otk_service: OtkPresentationService, category: str
) -> tuple[str, str]:
    listing = otk_service.list_presentations()
    for item in listing.items:
        card = otk_service.get_presentation(item.id)
        if card is None:
            continue
        for line in card.lines:
            if line.category == category:
                return card.id, line.id
    raise AssertionError(f"no seeded line with category={category}")


def test_sample_rule_depends_on_category(otk_service: OtkPresentationService) -> None:
    del otk_service  # pure compute helper
    metal = compute_line_sample_rule(
        {"category": "metal", "qty_fact": 100, "nomenclature": "лист"}
    )
    electronics = compute_line_sample_rule(
        {"category": "electronics", "qty_fact": 50, "nomenclature": "модуль"}
    )
    drawing = compute_line_sample_rule(
        {"category": "drawing_parts", "qty_fact": 100, "nomenclature": "деталь"}
    )
    pipes = compute_line_sample_rule(
        {
            "category": "pipes",
            "qty_fact": 100,
            "supplier_quality_rating": 40,
            "nomenclature": "труба",
        }
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

    # Прил. Б п.5 — 100%
    assert metal["sample_pct"] == 100.0
    assert metal["sample_size"] == 100
    assert metal["sample_basis"] == "100pct"

    # Прил. Б п.1 — 0–50 → 100%
    assert electronics["sample_pct"] == 100.0
    assert electronics["sample_size"] == 50
    assert electronics["sample_basis"] == "100pct"

    # Прил. Б п.3 — 51–100 → 50%
    assert drawing["sample_pct"] == 50.0
    assert drawing["sample_size"] == 50
    assert drawing["sample_basis"] == "50pct"

    # трубы: 100%, без скидки 1% по рейтингу
    assert pipes["sample_pct"] == 100.0
    assert pipes["sample_size"] == 100
    assert pipes["sample_basis"] == "100pct"

    # метизы: 51–100 → 5% + из каждой коробки
    assert fasteners["sample_pct"] == 5.0
    assert fasteners["sample_size"] == 5
    assert fasteners["sample_basis"] == "5pct"
    assert "коробк" in (fasteners.get("sample_note") or "").lower()

    assert rated["sample_pct"] == 1.0
    assert rated["sample_basis"] == "1pct_rating"
    assert rated["sample_size"] == 2


def test_list_and_get_seeded(otk_service: OtkPresentationService) -> None:
    listing = otk_service.list_presentations()
    assert len(listing.items) >= 1
    assert listing.earliest_due_at is not None
    assert len(listing.workers) == 3

    all_cards = [otk_service.get_presentation(item.id) for item in listing.items]
    assert all(c is not None for c in all_cards)
    total_lines = sum(len(c.lines) for c in all_cards if c is not None)
    assert total_lines >= 1
    categories = {line.category for c in all_cards if c for line in c.lines}
    assert "other" in categories or "metal" in categories or "electronics" in categories

    metal_card_id, _ = _first_card_with_category(otk_service, "metal")
    card = otk_service.get_presentation(metal_card_id)
    assert card is not None
    metal_line = next(line for line in card.lines if line.category == "metal")
    assert metal_line.sample_rule is not None
    assert metal_line.sample_rule.category == "metal"
    assert metal_line.sample_rule.sample_pct == 100.0


def test_update_line_category_recomputes_sample(
    otk_service: OtkPresentationService,
) -> None:
    """Category PATCH recomputes sample_rule atomically (UI must not snap back)."""
    pres_id, line_id = _first_card_with_category(otk_service, "metal")
    before = otk_service.get_presentation(pres_id)
    assert before is not None
    metal = next(line for line in before.lines if line.id == line_id)
    assert metal.sample_rule is not None
    assert metal.sample_rule.sample_basis == "100pct"
    lot_qty = metal.qty_fact if metal.qty_fact > 0 else metal.qty_upd

    updated = otk_service.update_line(
        pres_id,
        line_id,
        OtkShipmentLineUpdate(category="fasteners"),
    )
    assert updated is not None
    line = next(item for item in updated.lines if item.id == line_id)
    assert line.category == "fasteners"
    assert line.sample_rule is not None
    assert line.sample_rule.sample_pct is not None
    assert "коробк" in line.sample_rule.sample_note.lower()

    back = otk_service.update_line(
        pres_id,
        line_id,
        OtkShipmentLineUpdate(category="electronics"),
    )
    assert back is not None
    line2 = next(item for item in back.lines if item.id == line_id)
    assert line2.sample_rule is not None
    expected_electronics = compute_line_sample_rule(
        {
            "category": "electronics",
            "qty_fact": lot_qty,
            "nomenclature": line2.nomenclature,
        }
    )
    assert line2.sample_rule.sample_pct == expected_electronics["sample_pct"]
    assert line2.sample_rule.sample_basis == expected_electronics["sample_basis"]

    reread = otk_service.get_presentation(pres_id)
    assert reread is not None
    line3 = next(item for item in reread.lines if item.id == line_id)
    assert line3.category == "electronics"
    assert line3.sample_rule is not None
    assert line3.sample_rule.category == "electronics"
    assert line3.sample_rule.sample_pct == expected_electronics["sample_pct"]


def test_add_and_delete_line(otk_service: OtkPresentationService) -> None:
    listing = otk_service.list_presentations()
    assert listing.items
    pres_id = listing.items[0].id
    before = otk_service.get_presentation(pres_id)
    assert before is not None
    base_count = len(before.lines)

    added = otk_service.add_line(
        pres_id,
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
    # Прил. Б п.5 — 100% от 50 шт.
    assert added.lines[-1].sample_rule.sample_size == 50
    assert added.lines[-1].sample_rule.sample_pct == 100.0

    deleted = otk_service.delete_line(pres_id, new_id)
    assert deleted is not None
    assert len(deleted.lines) == base_count


def test_patch_presentation_header(otk_service: OtkPresentationService) -> None:
    listing = otk_service.list_presentations()
    assert listing.items
    pres_id = listing.items[0].id
    updated = otk_service.update_presentation(
        pres_id,
        OtkPresentationUpdate(status="in_progress", storage_zone="Зона X"),
    )
    assert updated is not None
    assert updated.status == "in_progress"
    assert updated.storage_zone == "Зона X"


def test_write_to_1c_stub(otk_service: OtkPresentationService) -> None:
    listing = otk_service.list_presentations()
    assert listing.items
    result = otk_service.write_check_to_1c(listing.items[0].id)
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
