from app.agents.document_analysis_agent.excel_service import (
    MergedNomenclatureRow,
    ScheduleProductPlan,
    _restrict_merged_rows_to_schedule_products,
)


def test_restrict_merged_rows_keeps_only_schedule_products() -> None:
    row = MergedNomenclatureRow(
        nomenclature="TYTAN test",
        products=[
            'FPV-перехватчик "СОКОЛ" И (день)',
            "Сокол И",
            "Сокол Т",
        ],
        quantity=15.0,
        by_product={
            'FPV-перехватчик "СОКОЛ" И (день)': 15.0,
            "Сокол И": 15.0,
            "Сокол Т": 15.0,
        },
    )
    schedule_plans = [
        ScheduleProductPlan(
            product='FPV-перехватчик "СОКОЛ" И (день)',
            monthly_qty={},
        ),
        ScheduleProductPlan(
            product='FPV-перехватчик "СОКОЛ" Т (ночь)',
            monthly_qty={},
        ),
    ]

    _restrict_merged_rows_to_schedule_products([row], schedule_plans)

    assert row.products == ['FPV-перехватчик "СОКОЛ" И (день)']
    assert row.by_product == {'FPV-перехватчик "СОКОЛ" И (день)': 15.0}
    assert row.quantity == 15.0
