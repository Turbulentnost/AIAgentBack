from app.agents.document_analysis_agent.onec_db_sources import (
    DbSpecCatalogEntry,
    build_product_spec_hints,
    build_stock_index_from_db,
    lookup_product_spec_hint,
    match_product_to_db_spec,
    _valid_spec_ref_key,
)
from app.agents.document_analysis_agent.excel_service import ScheduleProductPlan
from app.services.spec_nomenclature_match import EMPTY_GUID


def test_match_product_to_db_spec_finds_by_spec_hint() -> None:
    catalog = [
        DbSpecCatalogEntry(
            ref_key="abc",
            label="Сокол день 1.01 (ascent)",
            description='FPV-перехватчик "Сокол" день 1.01 (ascent)',
            main_product_name="",
            code="00002",
        )
    ]
    entry, reason = match_product_to_db_spec(
        'FPV-перехватчик "Сокол" (И-1.01 (ascent))',
        'FPV-перехватчик "Сокол" (И-1.01 (ascent))',
        catalog,
        spec_hint='FPV-перехватчик "Сокол" день 1.01 (ascent)',
    )
    assert entry is not None
    assert entry.ref_key == "abc"
    assert "спецификация плана" in reason


def test_match_product_to_db_spec_finds_by_main_product() -> None:
    catalog = [
        DbSpecCatalogEntry(
            ref_key="abc",
            label="Сокол-И ночь",
            description="Спецификация Сокол-И ночь",
            main_product_name="FPV перехватчик Сокол-И ночь",
            code="00001",
        )
    ]
    entry, reason = match_product_to_db_spec(
        "FPV перехватчик Сокол-И ночь",
        "FPV перехватчик Сокол-И ночь",
        catalog,
    )
    assert entry is not None
    assert entry.ref_key == "abc"
    assert "БД" in reason


def test_match_product_to_db_spec_empty_catalog() -> None:
    entry, reason = match_product_to_db_spec("A", "B", [])
    assert entry is None
    assert "нет спецификаций" in reason


def test_build_product_spec_hints_normalized_lookup() -> None:
    plans = [
        ScheduleProductPlan(
            product='FPV-перехватчик "Сокол" Р (Z40)',
            spec_name='FPV-перехватчик "Сокол" Р (Z40)',
            spec_ref_key="abc-123",
        )
    ]
    hints = build_product_spec_hints(plans)
    spec_hint, spec_ref = lookup_product_spec_hint(
        'FPV-перехватчик "Сокол" Р (Z40)',
        hints,
    )
    assert spec_hint.endswith("(Z40)")
    assert spec_ref == "abc-123"
    _, spec_ref_norm = lookup_product_spec_hint(
        'fpv-перехватчик "сокол" р (z40)',
        hints,
    )
    assert spec_ref_norm == "abc-123"


def test_build_product_spec_hints_prefers_row_with_ref_key_and_name() -> None:
    plans = [
        ScheduleProductPlan(
            product="Изделие A",
            spec_name="Спека без ключа",
            spec_ref_key="",
        ),
        ScheduleProductPlan(
            product="Изделие A",
            spec_name="Спека полная",
            spec_ref_key="guid-1",
        ),
    ]
    hints = build_product_spec_hints(plans)
    spec_hint, spec_ref = lookup_product_spec_hint("Изделие A", hints)
    assert spec_hint == "Спека полная"
    assert spec_ref == "guid-1"


def test_valid_spec_ref_key_rejects_empty_guid() -> None:
    assert _valid_spec_ref_key(EMPTY_GUID) == ""
    assert _valid_spec_ref_key("  abc  ") == "abc"
