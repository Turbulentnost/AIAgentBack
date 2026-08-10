from app.agents.document_analysis_agent.excel_service import (
    MergedNomenclatureRow,
    _enrich_merged_with_units,
)
from app.agents.document_analysis_agent.onec_db_sources import DbNomenclatureUnitEntry


def test_enrich_merged_with_units_fills_missing_only() -> None:
    rows = [
        MergedNomenclatureRow(
            nomenclature="Болт М6",
            products=["Изделие 1"],
            quantity=1.0,
            unit=None,
        ),
        MergedNomenclatureRow(
            nomenclature="Гайка М6",
            products=["Изделие 1"],
            quantity=2.0,
            unit="компл",
        ),
    ]
    index = {
        "болт м6": DbNomenclatureUnitEntry(nomenclature="Болт М6", unit="шт"),
        "гайка м6": DbNomenclatureUnitEntry(nomenclature="Гайка М6", unit="шт"),
    }
    _enrich_merged_with_units(rows, index)
    assert rows[0].unit == "шт"
    assert rows[1].unit == "компл"
