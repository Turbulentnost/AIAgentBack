from app.agents.document_analysis_agent.onec_db_sources import (
    DbSpecCatalogEntry,
    build_stock_index_from_db,
    match_product_to_db_spec,
)


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
