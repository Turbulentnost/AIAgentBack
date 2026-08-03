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
def otk_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OtkPresentationService:
    monkeypatch.setenv("OTK_USE_MOCK_STORE", "1")
    store = reset_otk_store_for_tests(tmp_path / "otk_presentations.json")
    return OtkPresentationService(store=store)


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


@pytest.mark.asyncio
async def test_list_and_get_seeded(otk_service: OtkPresentationService) -> None:
    listing = await otk_service.list_presentations()
    assert len(listing.items) == 3
    assert listing.pending_count == 3
    assert listing.earliest_due_at is not None
    assert len(listing.workers) == 3

    card = await otk_service.get_presentation("pres-001")
    assert card is not None
    assert len(card.lines) == 2
    assert card.lines[0].sample_rule is not None
    assert card.lines[0].sample_rule.category == "metal"
    assert card.lines[0].sample_rule.sample_pct == 10.0


@pytest.mark.asyncio
async def test_update_line_category_recomputes_sample(
    otk_service: OtkPresentationService,
) -> None:
    before = await otk_service.get_presentation("pres-001")
    assert before is not None
    line_id = before.lines[0].id
    assert before.lines[0].sample_rule is not None
    assert before.lines[0].sample_rule.sample_basis == "10pct"

    updated = await otk_service.update_line(
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


@pytest.mark.asyncio
async def test_add_and_delete_line(otk_service: OtkPresentationService) -> None:
    added = await otk_service.add_line(
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
    assert len(added.lines) == 2
    new_id = added.lines[-1].id
    assert added.lines[-1].sample_rule is not None
    assert added.lines[-1].sample_rule.sample_size == 5

    deleted = await otk_service.delete_line("pres-003", new_id)
    assert deleted is not None
    assert len(deleted.lines) == 1


@pytest.mark.asyncio
async def test_patch_presentation_header(otk_service: OtkPresentationService) -> None:
    updated = await otk_service.update_presentation(
        "pres-001",
        OtkPresentationUpdate(status="in_progress", storage_zone="Зона X"),
    )
    assert updated is not None
    assert updated.status == "in_progress"
    assert updated.storage_zone == "Зона X"


@pytest.mark.asyncio
async def test_write_to_1c_stub(otk_service: OtkPresentationService) -> None:
    result = await otk_service.write_check_to_1c("pres-001")
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
