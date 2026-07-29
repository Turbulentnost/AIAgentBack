"""JSON-backed material bank: warehouses + stock + 100 coverage suppliers."""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "coverage_seed.json"

_STORE: "MaterialBankStore | None" = None


def _dec(value: Any) -> Decimal:
    return Decimal(str(value or 0))


class MaterialBankStore:
    """Thread-safe in-memory view over coverage_seed.json."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _DEFAULT_PATH
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        with _LOCK:
            if not self.path.exists():
                raise FileNotFoundError(f"Coverage seed not found: {self.path}")
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("coverage_seed.json must be an object")
            raw.setdefault("materials", [])
            raw.setdefault("warehouses", [])
            raw.setdefault("stock", [])
            raw.setdefault("suppliers", [])
            return raw

    def reload(self) -> None:
        self._data = self._load()

    def snapshot(self) -> dict[str, Any]:
        with _LOCK:
            return deepcopy(self._data)

    def warehouses(self) -> list[dict[str, Any]]:
        return deepcopy(self._data.get("warehouses") or [])

    def stock_lines(self) -> list[dict[str, Any]]:
        return deepcopy(self._data.get("stock") or [])

    def suppliers(self) -> list[dict[str, Any]]:
        return deepcopy(self._data.get("suppliers") or [])

    def materials(self) -> list[dict[str, Any]]:
        return deepcopy(self._data.get("materials") or [])

    def active_suppliers(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self.suppliers()
            if item.get("is_active", True) and item.get("supplier_id")
        ]

    def bank_totals(self) -> dict[str, Any]:
        """Aggregate available bank qty by nomenclature (warehouse + supplier)."""
        warehouse_by_nom: dict[str, Decimal] = {}
        for line in self.stock_lines():
            key = str(line.get("nomenclature_id") or "").strip()
            if not key:
                continue
            available = _dec(line.get("quantity")) - _dec(line.get("reserved"))
            if available < 0:
                available = Decimal("0")
            warehouse_by_nom[key] = warehouse_by_nom.get(key, Decimal("0")) + available

        supplier_by_nom: dict[str, Decimal] = {}
        for supplier in self.active_suppliers():
            for offering in supplier.get("offerings") or []:
                if not isinstance(offering, dict):
                    continue
                key = str(offering.get("nomenclature_id") or "").strip()
                if not key:
                    continue
                qty = _dec(
                    offering.get("available_quantity", offering.get("available_qty"))
                )
                if qty <= 0:
                    continue
                supplier_by_nom[key] = supplier_by_nom.get(key, Decimal("0")) + qty

        keys = sorted(set(warehouse_by_nom) | set(supplier_by_nom))
        lines = []
        warehouse_total = Decimal("0")
        supplier_total = Decimal("0")
        for key in keys:
            wh = warehouse_by_nom.get(key, Decimal("0"))
            sp = supplier_by_nom.get(key, Decimal("0"))
            warehouse_total += wh
            supplier_total += sp
            lines.append(
                {
                    "nomenclature_id": key,
                    "warehouse_quantity": wh,
                    "supplier_quantity": sp,
                    "total_quantity": wh + sp,
                }
            )
        return {
            "warehouses_count": len(self.warehouses()),
            "suppliers_count": len(self.active_suppliers()),
            "stock_lines_count": len(self.stock_lines()),
            "warehouse_quantity_total": warehouse_total,
            "supplier_quantity_total": supplier_total,
            "bank_quantity_total": warehouse_total + supplier_total,
            "by_nomenclature": lines,
        }

    def supplier_price_bounds(self) -> dict[str, dict[str, Any]]:
        """Min/max offering prices keyed by casefolded nomenclature_id."""
        from app.agents.procurement_manager_agent.pricing import supplier_price_bounds

        return supplier_price_bounds(self)

    def to_public(self) -> dict[str, Any]:
        totals = self.bank_totals()
        bounds = self.supplier_price_bounds()
        return {
            "warehouses": self.warehouses(),
            "stock": self.stock_lines(),
            "suppliers": self.active_suppliers(),
            "materials": self.materials(),
            "price_bounds": [
                {
                    "nomenclature_id": item["nomenclature_id"],
                    "nomenclature_name": item.get("nomenclature_name"),
                    "price_min": str(item["price_min"]),
                    "price_max": str(item["price_max"]),
                    "offer_count": item["offer_count"],
                    "suppliers_count": item["suppliers_count"],
                }
                for item in sorted(
                    bounds.values(),
                    key=lambda row: str(row.get("nomenclature_id") or ""),
                )
            ],
            "totals": {
                "warehouses_count": totals["warehouses_count"],
                "suppliers_count": totals["suppliers_count"],
                "stock_lines_count": totals["stock_lines_count"],
                "warehouse_quantity_total": str(totals["warehouse_quantity_total"]),
                "supplier_quantity_total": str(totals["supplier_quantity_total"]),
                "bank_quantity_total": str(totals["bank_quantity_total"]),
            },
        }


def get_material_bank(path: Path | None = None) -> MaterialBankStore:
    global _STORE
    if path is not None:
        return MaterialBankStore(path)
    if _STORE is None:
        _STORE = MaterialBankStore()
    return _STORE


def reset_material_bank_for_tests(path: Path | None = None) -> MaterialBankStore:
    """Reload singleton from seed (or alternate path) for tests."""
    global _STORE
    _STORE = MaterialBankStore(path) if path is not None else MaterialBankStore()
    return _STORE


__all__ = [
    "MaterialBankStore",
    "get_material_bank",
    "reset_material_bank_for_tests",
]
