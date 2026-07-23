"""JSON-backed store for OTK presentation cards (MVP, no new DB table).

Persistence choice: file JSON under the agent package (seeded with mock cards).
Procurement case metadata wiring was heavier than needed for this UI MVP.
"""

from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "otk_presentations.json"

SEED_WORKERS: list[dict[str, Any]] = [
    {"id": "otk-w-1", "name": "Иванова А.С.", "position": "Инженер по качеству"},
    {"id": "otk-w-2", "name": "Петров Д.И.", "position": "Инженер по качеству"},
    {"id": "otk-w-3", "name": "Сидорова М.В.", "position": "Инженер ОТК"},
]

SEED_PRESENTATIONS: list[dict[str, Any]] = [
    {
        "id": "pres-001",
        "organization": "ООО НПО «Турбулентность-Дон»",
        "purchase_order": "ЗП-0001247",
        "supplier": "ООО «МеталлСервис»",
        "counterparty": "ООО «МеталлСервис»",
        "warehouse": "Склад сырья №1",
        "invoice_date": "2026-07-21",
        "invoice_number": "УПД-45821",
        "storage_zone": "Зона приёмки А",
        "presentation_place": "Участок входного контроля",
        "otk_incoming_warehouse": "Склад входного контроля ОТК",
        "executor_id": "otk-w-1",
        "due_at": "2026-07-23T17:00:00+03:00",
        "status": "queued",
        "lines": [
            {
                "id": "l1",
                "code": "10.01.00125",
                "nomenclature": "Лист стальной 3 мм Ст3",
                "storage_unit": "шт",
                "qty_upd": 120,
                "qty_fact": 120,
                "category": "metal",
            },
            {
                "id": "l2",
                "code": "10.01.00402",
                "nomenclature": "Труба бесшовная Ø57×3,5",
                "storage_unit": "м",
                "qty_upd": 48,
                "qty_fact": 48,
                "category": "pipes",
            },
        ],
    },
    {
        "id": "pres-002",
        "organization": "ООО НПО «Турбулентность-Дон»",
        "purchase_order": "ЗП-0001302",
        "supplier": "АО «КабельПром»",
        "counterparty": "АО «КабельПром»",
        "warehouse": "Склад комплектующих",
        "invoice_date": "2026-07-20",
        "invoice_number": "УПД-11209",
        "storage_zone": "Зона приёмки Б",
        "presentation_place": "Стол предъявления №2",
        "otk_incoming_warehouse": "Склад входного контроля ОТК",
        "executor_id": "otk-w-2",
        "due_at": "2026-07-22T16:00:00+03:00",
        "status": "in_progress",
        "lines": [
            {
                "id": "l3",
                "code": "20.05.00088",
                "nomenclature": "Кабель ВВГнг 3×2,5",
                "storage_unit": "м",
                "qty_upd": 500,
                "qty_fact": 498,
                "category": "cable",
                "supplier_quality_rating": 40,
            },
            {
                "id": "l4",
                "code": "30.02.00015",
                "nomenclature": "Болт М8×40 DIN 933",
                "storage_unit": "шт",
                "qty_upd": 2000,
                "qty_fact": 2000,
                "category": "fasteners",
            },
        ],
    },
    {
        "id": "pres-003",
        "organization": "ООО НПО «Турбулентность-Дон»",
        "purchase_order": "ЗП-0001310",
        "supplier": "ООО «ЭлектроКомпонент»",
        "counterparty": "ООО «ЭлектроКомпонент»",
        "warehouse": "Склад электроники",
        "invoice_date": "2026-07-22",
        "invoice_number": "УПД-9901",
        "storage_zone": "Зона приёмки В",
        "presentation_place": "Участок входного контроля",
        "otk_incoming_warehouse": "Склад входного контроля ОТК",
        "executor_id": "otk-w-3",
        "due_at": "2026-07-25T17:00:00+03:00",
        "status": "queued",
        "lines": [
            {
                "id": "l5",
                "code": "40.11.00003",
                "nomenclature": "Микросхема STM32F103",
                "storage_unit": "шт",
                "qty_upd": 80,
                "qty_fact": 80,
                "category": "electronics",
            },
        ],
    },
]


class OtkPresentationStore:
    """Thread-safe JSON file store for OTK cards."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _DEFAULT_PATH
        self._ensure_seeded()

    def _ensure_seeded(self) -> None:
        with _LOCK:
            if self.path.exists():
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "workers": deepcopy(SEED_WORKERS),
                "presentations": deepcopy(SEED_PRESENTATIONS),
            }
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _read(self) -> dict[str, Any]:
        with _LOCK:
            self._ensure_seeded()
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {"workers": deepcopy(SEED_WORKERS), "presentations": []}
            data.setdefault("workers", deepcopy(SEED_WORKERS))
            data.setdefault("presentations", [])
            return data

    def _write(self, data: dict[str, Any]) -> None:
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)

    def list_workers(self) -> list[dict[str, Any]]:
        return deepcopy(self._read().get("workers") or [])

    def list_presentations(self) -> list[dict[str, Any]]:
        return deepcopy(self._read().get("presentations") or [])

    def get_presentation(self, presentation_id: str) -> dict[str, Any] | None:
        for item in self.list_presentations():
            if item.get("id") == presentation_id:
                return item
        return None

    def save_presentation(self, card: dict[str, Any]) -> dict[str, Any]:
        with _LOCK:
            data = self._read()
            items: list[dict[str, Any]] = list(data.get("presentations") or [])
            found = False
            for idx, item in enumerate(items):
                if item.get("id") == card.get("id"):
                    items[idx] = deepcopy(card)
                    found = True
                    break
            if not found:
                items.append(deepcopy(card))
            data["presentations"] = items
            self._write(data)
            return deepcopy(card)

    def delete_line(self, presentation_id: str, line_id: str) -> dict[str, Any] | None:
        card = self.get_presentation(presentation_id)
        if card is None:
            return None
        lines = [line for line in (card.get("lines") or []) if line.get("id") != line_id]
        if len(lines) == len(card.get("lines") or []):
            return None
        card["lines"] = lines
        return self.save_presentation(card)

    @staticmethod
    def new_line_id() -> str:
        return f"l-{uuid.uuid4()}"


_STORE: OtkPresentationStore | None = None


def get_otk_store(path: Path | None = None) -> OtkPresentationStore:
    global _STORE
    if path is not None:
        return OtkPresentationStore(path)
    if _STORE is None:
        _STORE = OtkPresentationStore()
    return _STORE


def reset_otk_store_for_tests(path: Path) -> OtkPresentationStore:
    """Replace default singleton with a fresh store at `path` (tests)."""
    global _STORE
    if path.exists():
        path.unlink()
    _STORE = OtkPresentationStore(path)
    return _STORE


__all__ = [
    "OtkPresentationStore",
    "SEED_PRESENTATIONS",
    "SEED_WORKERS",
    "get_otk_store",
    "reset_otk_store_for_tests",
]
