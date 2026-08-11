"""Sync Grafana pie chart colors so departments share colors across panels."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.metrics.department_colors import (  # noqa: E402
    collect_known_department_names,
    department_chart_label,
    grafana_department_color_overrides,
)

DASHBOARD_PATH = ROOT / "monitoring" / "grafana" / "dashboards" / "agent-pochta.json"
PIE_PANEL_IDS = {11, 12}


def _backup_dashboard(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = ROOT / "backups" / f"grafana_department_colors_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / path.name
    shutil.copy2(path, backup_path)
    return backup_path


def sync_dashboard(path: Path = DASHBOARD_PATH) -> int:
    names = collect_known_department_names(ROOT)
    labels = [department_chart_label(name) for name in names]
    overrides = grafana_department_color_overrides(labels)

    dashboard = json.loads(path.read_text(encoding="utf-8"))
    updated = 0
    for panel in dashboard.get("panels") or []:
        if panel.get("id") not in PIE_PANEL_IDS:
            continue
        field_config = panel.setdefault("fieldConfig", {})
        field_config["overrides"] = overrides
        defaults = field_config.setdefault("defaults", {})
        defaults["color"] = {"mode": "fixed", "fixedColor": "transparent"}
        updated += 1

    if updated != len(PIE_PANEL_IDS):
        raise RuntimeError(f"Expected {len(PIE_PANEL_IDS)} pie panels, updated {updated}")

    backup_path = _backup_dashboard(path)
    path.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Backup: {backup_path}")
    print(f"Updated {updated} pie panels with {len(overrides)} department color overrides")
    return len(overrides)


if __name__ == "__main__":
    sync_dashboard()
