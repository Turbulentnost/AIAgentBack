"""Demo order definitions for the procurement-manager workspace.

Used by `scripts/_seed_procurement_manager_orders.py` and unit tests.
OTK presentations are a separate dataset — do not confuse the two.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.agents.procurement_manager_agent.batches import split_meter_pieces

AGENT_ID = "procurement_logistics_agent"
DEMO_TAG = "procurement_manager_demo_v1"
DEMO_CASE_1 = uuid.UUID("685dbc88-3ee6-4f0d-8dd8-347ad930e89e")
DEMO_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

DATA_PATH = Path(__file__).resolve().parent / "data" / "demo_orders.json"

PROJECTS = [
    {
        "project_code": "PRJ-ТД-2026-01",
        "project_name": "Модернизация стенда турбоагрегатов",
        "department": "Монтажный участок №2",
    },
    {
        "project_code": "PRJ-ТД-2026-02",
        "project_name": "Линия сборки насосных агрегатов",
        "department": "Сборочный цех",
    },
    {
        "project_code": "PRJ-ТД-2026-03",
        "project_name": "Реконструкция цеха механообработки",
        "department": "Цех механообработки",
    },
    {
        "project_code": "PRJ-ТД-2026-04",
        "project_name": "Система АСУ ТП компрессорной",
        "department": "АСУ ТП",
    },
    {
        "project_code": "PRJ-ТД-2026-05",
        "project_name": "Производство корпусов редукторов",
        "department": "Литейный участок",
    },
    {
        "project_code": "PRJ-ТД-2026-06",
        "project_name": "Капитальный ремонт испытательного полигона",
        "department": "Испытательный полигон",
    },
    {
        "project_code": "PRJ-ТД-2026-07",
        "project_name": "Электроснабжение производственного корпуса",
        "department": "Энергоцех",
    },
]

MATERIALS: list[tuple[str, str, str]] = [
    ("steel", "Сталь 20 лист 5 мм", "кг"),
    ("10.01.00125", "Лист стальной 3 мм Ст3", "шт"),
    ("10.01.00402", "Труба бесшовная Ø57×3,5", "м"),
    ("20.05.00088", "Кабель ВВГнг 3×2,5", "м"),
    ("30.02.00015", "Болт М8×40 DIN 933", "шт"),
    ("40.11.00003", "Микросхема STM32F103", "шт"),
    ("10.02.00010", "Уголок 50×50×5", "м"),
    ("10.03.00044", "Швеллер 12П", "м"),
    ("20.01.00012", "Подшипник 6205-2RS", "шт"),
    ("20.02.00033", "Ремень клиновой A-1250", "шт"),
    ("30.01.00007", "Гайка М8 DIN 934", "шт"),
    ("30.03.00021", "Шайба 8 DIN 125", "шт"),
    ("40.01.00055", "Контактор КМИ-22510", "шт"),
    ("40.02.00018", "Автомат ВА47-29 16А", "шт"),
    ("50.01.00001", "Краска ГФ-021 серая", "кг"),
    ("50.02.00009", "Электрод МР-3 Ø3", "кг"),
    ("60.01.00014", "Масло индустриальное И-40А", "л"),
    ("70.01.00002", "Фильтр воздушный", "шт"),
    ("70.02.00011", "Манжета 40×60×10", "шт"),
    ("80.01.00005", "Профиль алюминиевый 40×40", "м"),
    ("missing-custom-bracket", "Кронштейн спец. (нет в банке)", "шт"),
    ("missing-seal-kit", "Комплект уплотнений спец.", "компл"),
]

WAREHOUSES = [
    "Склад сырья №1",
    "Склад комплектующих",
    "Склад электроники",
]

# Cycle through manager fulfillment categories (case.status → UI filter).
# 5 each for 30 orders: no_supplier, payment, delivery, otk, posting, completed.
FULFILLMENT_CYCLE: list[tuple[str, str]] = [
    ("purchase_draft", "no_supplier"),
    ("payment_pending", "payment"),
    ("in_transit", "delivery"),
    ("receiving", "otk_presentation"),
    ("posting_required", "posting"),
    ("posted", "completed"),
]


def case_id_for_index(index: int) -> uuid.UUID:
    if index == 1:
        return DEMO_CASE_1
    return uuid.uuid5(DEMO_NAMESPACE, f"procurement-manager-demo-{index:02d}")


def build_orders(*, now: datetime | None = None) -> list[dict[str, Any]]:
    base = now or datetime.now(UTC)
    orders: list[dict[str, Any]] = []
    for index in range(1, 31):
        project = PROJECTS[(index - 1) % len(PROJECTS)]
        status, fulfillment_status = FULFILLMENT_CYCLE[(index - 1) % len(FULFILLMENT_CYCLE)]
        required = base + timedelta(days=(index % 12) - 2, hours=10)
        line_count = 2 + ((index * 3) % 5)  # 2..6
        positions: list[dict[str, Any]] = []
        for line_no in range(1, line_count + 1):
            mat_idx = (index * 5 + line_no * 3) % len(MATERIALS)
            nom_id, nom_name, unit = MATERIALS[mat_idx]
            qty = Decimal(str(5 + ((index * 7 + line_no * 11) % 90)))
            if line_no == 1 and index % 4 == 0:
                qty = Decimal(str(180 + index))
            # Meter goods: keep total as sum of physical cuts (e.g. 5.1 + 6.3).
            meter_pieces: list[str] | None = None
            if unit in {"м", "m"}:
                # Prefer totals that split into several pipes (8–28 m).
                if qty < 8:
                    qty = Decimal(str(8 + (index + line_no) % 12))
                pieces = split_meter_pieces(qty, seed=index * 10 + line_no)
                qty = sum(pieces, Decimal("0"))
                meter_pieces = [str(p) for p in pieces]
            pos_row: dict[str, Any] = {
                "line_id": f"pm-{index:02d}-L{line_no}",
                "line_number": line_no,
                "nomenclature_id": nom_id,
                "nomenclature_name": nom_name,
                "unit": unit,
                "quantity": str(qty),
                "required_date": required.isoformat(),
            }
            if meter_pieces:
                pos_row["meter_pieces"] = meter_pieces
            positions.append(pos_row)
        order_uuid = case_id_for_index(index)
        source_number = f"ЗП-DEMO-{index:04d}"
        orders.append(
            {
                "id": str(order_uuid),
                "source_number": source_number,
                "source_type": "production_material_order",
                "source_1c_ref": f"demo-pm-order-{index:03d}",
                "source_database": "demo",
                "status": status,
                "fulfillment_status": fulfillment_status,
                "current_agent_id": AGENT_ID,
                "department_name": project["department"],
                "warehouse_name": WAREHOUSES[(index - 1) % len(WAREHOUSES)],
                "required_date": required.isoformat(),
                "project_code": project["project_code"],
                "project_name": project["project_name"],
                "need_title": f"{project['project_name']} · {source_number}",
                "positions": positions,
            }
        )
    return orders


def write_fixture(orders: list[dict[str, Any]] | None = None, path: Path | None = None) -> Path:
    payload_orders = orders if orders is not None else build_orders()
    out = path or DATA_PATH
    payload = {
        "version": 1,
        "tag": DEMO_TAG,
        "demo_case_id": str(DEMO_CASE_1),
        "ui_url": (
            "http://127.0.0.1:5173/agents/procurement-manager"
            f"?case={DEMO_CASE_1}"
        ),
        "orders_count": len(payload_orders),
        "orders": payload_orders,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


__all__ = [
    "AGENT_ID",
    "DATA_PATH",
    "DEMO_CASE_1",
    "DEMO_NAMESPACE",
    "DEMO_TAG",
    "MATERIALS",
    "PROJECTS",
    "build_orders",
    "case_id_for_index",
    "write_fixture",
]
