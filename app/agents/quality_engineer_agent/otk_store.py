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


def _load_packaged_seed() -> dict[str, Any]:
    """Load workers/presentations from packaged JSON (single source of truth)."""
    raw = json.loads(_DEFAULT_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {"workers": [], "presentations": []}
    return {
        "workers": list(raw.get("workers") or []),
        "presentations": list(raw.get("presentations") or []),
    }


_PACKAGED = _load_packaged_seed()
SEED_WORKERS: list[dict[str, Any]] = list(_PACKAGED["workers"])
SEED_PRESENTATIONS: list[dict[str, Any]] = list(_PACKAGED["presentations"])


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
