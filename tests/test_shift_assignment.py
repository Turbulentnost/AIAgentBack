from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.agents.document_analysis_agent.excel_service import DetailedScheduleExtract
from app.agents.document_analysis_agent.shift_assignment import collect_result_purchase_tasks


def _row(
    name: str,
    *,
    daily_forecast: dict[str, float] | None = None,
    monthly_forecast: dict[str, float] | None = None,
    stock: float = 0.0,
):
    return SimpleNamespace(
        nomenclature=name,
        daily_forecast=daily_forecast or {},
        monthly_forecast=monthly_forecast or {},
        stock=stock,
        supplier="Поставщик",
        country_of_origin="Китай",
    )


def test_purchase_tasks_use_daily_week_horizon_when_detailed_plan_exists() -> None:
    detailed = DetailedScheduleExtract(
        files=[],
        plans=[],
        year=2026,
        month=8,
        day_keys=[
            "2026-08-03",
            "2026-08-04",
            "2026-08-05",
            "2026-08-06",
            "2026-08-07",
            "2026-08-08",
            "2026-08-09",
        ],
    )
    rows = [
        _row(
            "Недельный дефицит",
            daily_forecast={
                "2026-08-06": -100,
                "2026-08-07": 5,
                "2026-08-08": -20,
                "2026-08-09": -50,
            },
            monthly_forecast={"Август": -999},
            stock=10,
        ),
        _row(
            "Только месячный дефицит",
            daily_forecast={
                "2026-08-07": 10,
                "2026-08-08": 5,
                "2026-08-09": 1,
            },
            monthly_forecast={"Август": -500},
            stock=10,
        ),
    ]

    tasks = collect_result_purchase_tasks(rows, detailed, as_of=date(2026, 8, 7))

    assert [task.nomenclature for task in tasks] == ["Недельный дефицит"]
    assert tasks[0].deficit_label == "50"
    assert tasks[0].due_label == "08.08.2026"
    assert tasks[0].priority == "today"
    assert "дневному прогнозу" in tasks[0].problem


def test_purchase_tasks_fallback_to_monthly_without_detailed_plan() -> None:
    rows = [
        _row(
            "Месячный дефицит",
            monthly_forecast={"Август": -500},
            stock=10,
        )
    ]

    tasks = collect_result_purchase_tasks(rows, None, as_of=date(2026, 8, 7))

    assert [task.nomenclature for task in tasks] == ["Месячный дефицит"]
    assert tasks[0].deficit_label == "500"
    assert tasks[0].due_label == "31.08.2026"
