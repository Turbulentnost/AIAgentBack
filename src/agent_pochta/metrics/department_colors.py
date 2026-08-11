"""Stable department → color mapping for Grafana pie charts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_MAX_DEPARTMENT_LABEL_LEN = 64


def normalize_department_key(name: str) -> str:
    """Normalize department name for stable color hashing."""
    return " ".join(str(name).split()).strip().lower()


def department_chart_label(name: str) -> str:
    """Same label formatting as prometheus_exporter._department_label."""
    cleaned = " ".join(str(name).split()).strip()
    if not cleaned:
        return "(пусто)"
    if len(cleaned) > _MAX_DEPARTMENT_LABEL_LEN:
        return cleaned[: _MAX_DEPARTMENT_LABEL_LEN - 1] + "…"
    return cleaned


def _hsl_to_hex(hue: float, saturation: float, lightness: float) -> str:
    """Convert HSL (0-360, 0-100, 0-100) to #RRGGBB."""
    h = hue / 360.0
    s = saturation / 100.0
    l = lightness / 100.0

    if s == 0:
        channel = round(l * 255)
        return f"#{channel:02x}{channel:02x}{channel:02x}"

    def hue_to_rgb(p: float, q: float, t: float) -> float:
        if t < 0:
            t += 1
        if t > 1:
            t -= 1
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    r = hue_to_rgb(p, q, h + 1 / 3)
    g = hue_to_rgb(p, q, h)
    b = hue_to_rgb(p, q, h - 1 / 3)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def department_color(name: str) -> str:
    """Return a stable hex color for a department name (dark-theme friendly)."""
    key = normalize_department_key(name)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    hue = int(digest[:8], 16) % 360
    saturation = 58 + (int(digest[8:12], 16) % 22)
    lightness = 52 + (int(digest[12:16], 16) % 18)
    return _hsl_to_hex(hue, saturation, lightness)


def grafana_color_override(department_label: str) -> dict[str, Any]:
    """Grafana fieldConfig override for a single department series."""
    return {
        "matcher": {"id": "byName", "options": department_label},
        "properties": [
            {
                "id": "color",
                "value": {"mode": "fixed", "fixedColor": department_color(department_label)},
            }
        ],
    }


def grafana_department_color_overrides(department_labels: list[str]) -> list[dict[str, Any]]:
    """Build sorted Grafana overrides for pie chart department slices."""
    labels = sorted({department_chart_label(label) for label in department_labels if label})
    labels = [label for label in labels if label != "(пусто)"]
    return [grafana_color_override(label) for label in labels]


def _collect_department_names_from_obj(obj: Any, names: set[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "department_name" and isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    names.add(cleaned)
            else:
                _collect_department_names_from_obj(value, names)
    elif isinstance(obj, list):
        for item in obj:
            _collect_department_names_from_obj(item, names)


def collect_known_department_names(root: Path | None = None) -> list[str]:
    """Collect department names from allowlist, corrections and routing rules."""
    root = root or Path(__file__).resolve().parents[3]
    names: set[str] = set()

    allowlist_path = root / "data" / "ui_department_allowlist.json"
    if allowlist_path.is_file():
        data = json.loads(allowlist_path.read_text(encoding="utf-8"))
        for item in data.get("departments") or []:
            if isinstance(item, dict) and item.get("name"):
                names.add(str(item["name"]))

    corrections_path = root / "data" / "routing_corrections.json"
    if corrections_path.is_file():
        data = json.loads(corrections_path.read_text(encoding="utf-8"))
        for entry in data.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            for key in ("department_name", "original_department_name"):
                value = str(entry.get(key) or "").strip()
                if value:
                    names.add(value)

    rules_path = root / "data" / "routing_rules.json"
    if rules_path.is_file():
        data = json.loads(rules_path.read_text(encoding="utf-8"))
        _collect_department_names_from_obj(data, names)

    return sorted(names)
