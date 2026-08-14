"""Изделия без загруженной спецификации 1С не участвуют в расчёте обеспеченности."""

from __future__ import annotations

from datetime import date

from app.agents.document_analysis_agent.coverage_dashboard import build_coverage_dashboard
from app.agents.document_analysis_agent.excel_service import ProductSpecLink, SpecMaterialItem
from app.agents.document_analysis_agent.onec_db_sources import (
    DbSpecCatalogEntry,
    finalize_onec_spec_links,
    products_with_loaded_onec_specs,
)
from app.agents.document_analysis_agent.product_coverage import (
    DailyPlanCoverageResult,
    ProductBom,
    ProductDayCoverage,
    ProductCoverageResult,
    ProductMonthCoverage,
)


def _catalog(ref_key: str, label: str = "Спека") -> DbSpecCatalogEntry:
    return DbSpecCatalogEntry(
        ref_key=ref_key,
        label=label,
        description=label,
        main_product_name=label,
        code="",
    )


def test_products_with_loaded_onec_specs_requires_catalog_and_materials():
    links = [
        ProductSpecLink(
            schedule_product="Изделие А",
            spec_ref_key="aaa-bbb",
            status="matched",
        ),
        ProductSpecLink(
            schedule_product="Промышленный вентилятор",
            spec_ref_key="missing-ref",
            status="matched",
        ),
        ProductSpecLink(
            schedule_product="Изделие Б",
            spec_ref_key="ccc-ddd",
            status="unmatched",
        ),
    ]
    materials = {
        "aaa-bbb": [
            SpecMaterialItem(nomenclature="Болт", quantity=1.0, product="Изделие А"),
        ],
    }
    catalog = [_catalog("aaa-bbb"), _catalog("ccc-ddd")]

    eligible = products_with_loaded_onec_specs(links, materials, catalog)
    assert eligible == frozenset({"Изделие А"})


def test_finalize_onec_spec_links_demotes_invalid_matches():
    links = [
        ProductSpecLink(
            schedule_product="Промышленный вентилятор",
            spec_ref_key="ghost-ref",
            status="matched",
            reason="тест",
        ),
    ]
    demoted = finalize_onec_spec_links(links, {}, [_catalog("aaa-bbb")])
    assert demoted == 1
    assert links[0].status == "unmatched"


def test_coverage_dashboard_excludes_products_without_onec_spec():
    day = "2026-08-13"
    eligible = frozenset({"FPV Сокол"})
    daily = DailyPlanCoverageResult(
        day_keys=[day],
        products_in_order=["FPV Сокол", "Промышленный вентилятор"],
        boms={
            "FPV Сокол": ProductBom(product="FPV Сокол", matched=True, materials={"bolt": 1.0}),
            "Промышленный вентилятор": ProductBom(
                product="Промышленный вентилятор", matched=False
            ),
        },
        cells={
            ("FPV Сокол", day): ProductDayCoverage(
                product="FPV Сокол", day=day, plan=100.0, covered=50.0, fact=0.0
            ),
            ("Промышленный вентилятор", day): ProductDayCoverage(
                product="Промышленный вентилятор",
                day=day,
                plan=2000.0,
                covered=0.0,
                fact=0.0,
            ),
        },
    )
    payload = build_coverage_dashboard(
        daily_plan_coverage=daily,
        product_coverage=None,
        merged=[],
        day_keys=[day],
        as_of=date(2026, 8, 13),
        schedule_month="2026-08",
        spec_eligible_products=eligible,
    )
    assert payload is not None
    day_period = payload["periods"]["day"]
    names = [row["name"] for row in day_period["products"]["rows"]]
    assert "Промышленный вентилятор" not in names
    assert day_period["products"]["tiles"]["all"] == 1
    assert day_period["products"]["tiles"]["plan_total"] == 100.0


def test_coverage_dashboard_monthly_excludes_ineligible_products():
    month = "Август"
    eligible = frozenset({"Изделие А"})
    product_coverage = ProductCoverageResult(
        months=[month],
        products_in_order=["Изделие А", "Промышленный вентилятор"],
        boms={},
        cells={
            ("Изделие А", month): ProductMonthCoverage(
                product="Изделие А", month=month, plan=50.0, fact=0.0, covered=30.0
            ),
            ("Промышленный вентилятор", month): ProductMonthCoverage(
                product="Промышленный вентилятор",
                month=month,
                plan=2000.0,
                fact=0.0,
                covered=0.0,
            ),
        },
    )
    payload = build_coverage_dashboard(
        daily_plan_coverage=None,
        product_coverage=product_coverage,
        merged=[],
        day_keys=["2026-08-01"],
        as_of=date(2026, 8, 10),
        schedule_month="2026-08",
        spec_eligible_products=eligible,
    )
    assert payload is not None
    week = payload["periods"]["week"]
    names = [row["name"] for row in week["products"]["rows"]]
    assert "Промышленный вентилятор" not in names
    assert week["products"]["tiles"]["plan_total"] == 50.0
