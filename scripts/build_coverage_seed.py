"""Rebuild coverage_seed.json: typical order nomenclature + multi-supplier prices.

Run:
  py -3 scripts/build_coverage_seed.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "agents" / "procurement_manager_agent" / "data" / "coverage_seed.json"

# Typical / fixture nomenclature from manager case positions (partial coverage).
MATERIALS = [
    {"nomenclature_id": "steel", "nomenclature_name": "Сталь 20 лист 5 мм", "unit": "кг", "category": "metal"},
    {"nomenclature_id": "10.01.00125", "nomenclature_name": "Лист стальной 3 мм Ст3", "unit": "шт", "category": "metal"},
    {"nomenclature_id": "10.01.00402", "nomenclature_name": "Труба бесшовная Ø57×3,5", "unit": "м", "category": "pipes"},
    {"nomenclature_id": "20.05.00088", "nomenclature_name": "Кабель ВВГнг 3×2,5", "unit": "м", "category": "cable"},
    {"nomenclature_id": "30.02.00015", "nomenclature_name": "Болт М8×40 DIN 933", "unit": "шт", "category": "fasteners"},
    {"nomenclature_id": "40.11.00003", "nomenclature_name": "Микросхема STM32F103", "unit": "шт", "category": "electronics"},
    {"nomenclature_id": "10.02.00010", "nomenclature_name": "Уголок 50×50×5", "unit": "м", "category": "metal"},
    {"nomenclature_id": "10.03.00044", "nomenclature_name": "Швеллер 12П", "unit": "м", "category": "metal"},
    {"nomenclature_id": "20.01.00012", "nomenclature_name": "Подшипник 6205-2RS", "unit": "шт", "category": "bearings"},
    {"nomenclature_id": "20.02.00033", "nomenclature_name": "Ремень клиновой A-1250", "unit": "шт", "category": "drive"},
    {"nomenclature_id": "30.01.00007", "nomenclature_name": "Гайка М8 DIN 934", "unit": "шт", "category": "fasteners"},
    {"nomenclature_id": "30.03.00021", "nomenclature_name": "Шайба 8 DIN 125", "unit": "шт", "category": "fasteners"},
    {"nomenclature_id": "40.01.00055", "nomenclature_name": "Контактор КМИ-22510", "unit": "шт", "category": "electronics"},
    {"nomenclature_id": "40.02.00018", "nomenclature_name": "Автомат ВА47-29 16А", "unit": "шт", "category": "electronics"},
    {"nomenclature_id": "50.01.00001", "nomenclature_name": "Краска ГФ-021 серая", "unit": "кг", "category": "consumables"},
    {"nomenclature_id": "50.02.00009", "nomenclature_name": "Электрод МР-3 Ø3", "unit": "кг", "category": "consumables"},
    {"nomenclature_id": "60.01.00014", "nomenclature_name": "Масло индустриальное И-40А", "unit": "л", "category": "consumables"},
    {"nomenclature_id": "70.01.00002", "nomenclature_name": "Фильтр воздушный", "unit": "шт", "category": "spares"},
    {"nomenclature_id": "70.02.00011", "nomenclature_name": "Манжета 40×60×10", "unit": "шт", "category": "spares"},
    {"nomenclature_id": "80.01.00005", "nomenclature_name": "Профиль алюминиевый 40×40", "unit": "м", "category": "metal"},
]

# Base list prices — suppliers deviate ±% so min/max spreads are visible.
BASE_PRICES = {
    "steel": 120.0,
    "10.01.00125": 850.0,
    "10.01.00402": 310.0,
    "20.05.00088": 95.0,
    "30.02.00015": 4.5,
    "40.11.00003": 180.0,
    "10.02.00010": 140.0,
    "10.03.00044": 520.0,
    "20.01.00012": 260.0,
    "20.02.00033": 780.0,
    "30.01.00007": 1.8,
    "30.03.00021": 0.9,
    "40.01.00055": 1450.0,
    "40.02.00018": 390.0,
    "50.01.00001": 210.0,
    "50.02.00009": 175.0,
    "60.01.00014": 95.0,
    "70.01.00002": 640.0,
    "70.02.00011": 85.0,
    "80.01.00005": 430.0,
}

WAREHOUSES = [
    {"warehouse_id": "wh-raw-1", "name": "Склад сырья №1", "code": "RAW-01", "location": "Корпус А"},
    {"warehouse_id": "wh-comp-1", "name": "Склад комплектующих", "code": "CMP-01", "location": "Корпус Б"},
    {"warehouse_id": "wh-elec-1", "name": "Склад электроники", "code": "ELC-01", "location": "Корпус В"},
]

# Keep quantities used by allocation tests (steel warehouse = 170).
STOCK = [
    {"stock_id": "stock-001", "warehouse_id": "wh-raw-1", "nomenclature_id": "steel", "nomenclature_name": "Сталь 20 лист 5 мм", "quantity": 120, "unit": "кг", "reserved": 0},
    {"stock_id": "stock-002", "warehouse_id": "wh-comp-1", "nomenclature_id": "10.01.00125", "nomenclature_name": "Лист стальной 3 мм Ст3", "quantity": 80, "unit": "шт", "reserved": 0},
    {"stock_id": "stock-003", "warehouse_id": "wh-raw-1", "nomenclature_id": "10.01.00402", "nomenclature_name": "Труба бесшовная Ø57×3,5", "quantity": 200, "unit": "м", "reserved": 0},
    {"stock_id": "stock-004", "warehouse_id": "wh-comp-1", "nomenclature_id": "30.02.00015", "nomenclature_name": "Болт М8×40 DIN 933", "quantity": 1500, "unit": "шт", "reserved": 0},
    {"stock_id": "stock-005", "warehouse_id": "wh-elec-1", "nomenclature_id": "40.11.00003", "nomenclature_name": "Микросхема STM32F103", "quantity": 40, "unit": "шт", "reserved": 0},
    {"stock_id": "stock-006", "warehouse_id": "wh-raw-1", "nomenclature_id": "steel", "nomenclature_name": "Сталь 20 лист 5 мм", "quantity": 50, "unit": "кг", "reserved": 0},
    {"stock_id": "stock-007", "warehouse_id": "wh-comp-1", "nomenclature_id": "20.01.00012", "nomenclature_name": "Подшипник 6205-2RS", "quantity": 90, "unit": "шт", "reserved": 0},
    {"stock_id": "stock-008", "warehouse_id": "wh-elec-1", "nomenclature_id": "40.01.00055", "nomenclature_name": "Контактор КМИ-22510", "quantity": 25, "unit": "шт", "reserved": 0},
    {"stock_id": "stock-009", "warehouse_id": "wh-raw-1", "nomenclature_id": "10.02.00010", "nomenclature_name": "Уголок 50×50×5", "quantity": 160, "unit": "м", "reserved": 0},
    {"stock_id": "stock-010", "warehouse_id": "wh-comp-1", "nomenclature_id": "30.01.00007", "nomenclature_name": "Гайка М8 DIN 934", "quantity": 2000, "unit": "шт", "reserved": 0},
    {"stock_id": "stock-011", "warehouse_id": "wh-elec-1", "nomenclature_id": "40.02.00018", "nomenclature_name": "Автомат ВА47-29 16А", "quantity": 60, "unit": "шт", "reserved": 0},
    {"stock_id": "stock-012", "warehouse_id": "wh-raw-1", "nomenclature_id": "10.03.00044", "nomenclature_name": "Швеллер 12П", "quantity": 70, "unit": "м", "reserved": 0},
    {"stock_id": "stock-013", "warehouse_id": "wh-comp-1", "nomenclature_id": "20.02.00033", "nomenclature_name": "Ремень клиновой A-1250", "quantity": 35, "unit": "шт", "reserved": 0},
    {"stock_id": "stock-014", "warehouse_id": "wh-elec-1", "nomenclature_id": "20.05.00088", "nomenclature_name": "Кабель ВВГнг 3×2,5", "quantity": 300, "unit": "м", "reserved": 0},
    {"stock_id": "stock-015", "warehouse_id": "wh-raw-1", "nomenclature_id": "80.01.00005", "nomenclature_name": "Профиль алюминиевый 40×40", "quantity": 110, "unit": "м", "reserved": 0},
    {"stock_id": "stock-016", "warehouse_id": "wh-comp-1", "nomenclature_id": "50.01.00001", "nomenclature_name": "Краска ГФ-021 серая", "quantity": 45, "unit": "кг", "reserved": 0},
    {"stock_id": "stock-017", "warehouse_id": "wh-elec-1", "nomenclature_id": "70.01.00002", "nomenclature_name": "Фильтр воздушный", "quantity": 55, "unit": "шт", "reserved": 0},
    {"stock_id": "stock-018", "warehouse_id": "wh-raw-1", "nomenclature_id": "50.02.00009", "nomenclature_name": "Электрод МР-3 Ø3", "quantity": 80, "unit": "кг", "reserved": 0},
    {"stock_id": "stock-019", "warehouse_id": "wh-comp-1", "nomenclature_id": "60.01.00014", "nomenclature_name": "Масло индустриальное И-40А", "quantity": 120, "unit": "л", "reserved": 0},
    {"stock_id": "stock-020", "warehouse_id": "wh-elec-1", "nomenclature_id": "70.02.00011", "nomenclature_name": "Манжета 40×60×10", "quantity": 200, "unit": "шт", "reserved": 0},
    {"stock_id": "stock-021", "warehouse_id": "wh-raw-1", "nomenclature_id": "30.03.00021", "nomenclature_name": "Шайба 8 DIN 125", "quantity": 5000, "unit": "шт", "reserved": 0},
    {"stock_id": "stock-022", "warehouse_id": "wh-comp-1", "nomenclature_id": "30.02.00015", "nomenclature_name": "Болт М8×40 DIN 933", "quantity": 500, "unit": "шт", "reserved": 0},
    {"stock_id": "stock-023", "warehouse_id": "wh-elec-1", "nomenclature_id": "20.05.00088", "nomenclature_name": "Кабель ВВГнг 3×2,5", "quantity": 150, "unit": "м", "reserved": 0},
]

SUPPLIER_PREFIXES = [
    "МеталлСервис",
    "ПромСнаб",
    "ТехноТрейд",
    "СнабРегион",
    "Индустриум",
]


def _price_for(nom_id: str, supplier_index: int) -> float:
    base = BASE_PRICES[nom_id]
    # Deterministic spread: ~0.75x … 1.35x so several suppliers share items with different prices.
    factor = 0.75 + ((supplier_index * 7 + len(nom_id) * 3) % 61) / 100.0
    return round(base * factor, 2)


def build() -> dict:
    materials_by_id = {m["nomenclature_id"]: m for m in MATERIALS}
    suppliers = []
    for i in range(1, 101):
        prefix = SUPPLIER_PREFIXES[(i - 1) % len(SUPPLIER_PREFIXES)]
        # Each supplier offers 3 materials; rotate so every material appears on many suppliers.
        offer_idxs = [(i - 1 + offset) % len(MATERIALS) for offset in (0, 7, 13)]
        offerings = []
        categories: set[str] = set()
        for idx in offer_idxs:
            mat = MATERIALS[idx]
            categories.add(mat["category"])
            available_qty = 20 + ((i + idx * 3) % 80)
            offerings.append(
                {
                    "nomenclature_id": mat["nomenclature_id"],
                    "nomenclature_name": mat["nomenclature_name"],
                    # Capacity the supplier can fulfill (alias: available_qty).
                    "available_quantity": available_qty,
                    "available_qty": available_qty,
                    "unit": mat["unit"],
                    "lead_time_days": 3 + ((i + idx) % 10),
                    "unit_price": _price_for(mat["nomenclature_id"], i),
                }
            )
        suppliers.append(
            {
                "supplier_id": f"bank-sup-{i:03d}",
                "name": f"ООО «{prefix}-{i:03d}»",
                "tax_id": f"{7700000000 + i}",
                "source": "internal",
                "categories": sorted(categories),
                "quality_rating": 55 + (i % 40),
                "delivery_rating": 50 + ((i * 3) % 45),
                "commercial_rating": 52 + ((i * 5) % 43),
                "is_active": True,
                "contacts": {
                    "email": f"sales{i:03d}@supplier-bank.local",
                    "phone": f"+7-495-{100 + i:03d}-{10 + (i % 80):02d}-{20 + (i % 70):02d}",
                },
                "offerings": offerings,
                "evidence": ["coverage_seed"],
            }
        )

    # Ensure every material has ≥3 distinct supplier prices (overlap).
    for mat in MATERIALS:
        prices = {
            float(o["unit_price"])
            for s in suppliers
            for o in s["offerings"]
            if o["nomenclature_id"] == mat["nomenclature_id"]
        }
        if len(prices) < 3:
            raise RuntimeError(f"Need ≥3 prices for {mat['nomenclature_id']}, got {prices}")

    return {
        "version": 2,
        "materials": MATERIALS,
        "warehouses": WAREHOUSES,
        "stock": STOCK,
        "suppliers": suppliers,
        "meta": {
            "description": (
                "Partial coverage of typical manager-case nomenclature; "
                "same items offered by multiple suppliers with unit_price + available_qty "
                "(capacity) for top-3 price/coverage ranking."
            ),
            "materials_catalog": list(materials_by_id),
            "offering_fields": ["unit_price", "available_quantity", "available_qty"],
        },
    }


def main() -> None:
    payload = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({len(payload['suppliers'])} suppliers, {len(payload['materials'])} materials)")


if __name__ == "__main__":
    main()
