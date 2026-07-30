"""Side-by-side OData metadata for working vs broken agent attachments."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
CASES = {
    "760-ok": "27997dc5-8689-11f1-984a-6cb31113810e",
    "762-eml-broken": "be75422b-8720-11f1-984b-6cb31113810e",
    "762-msg-broken": "18516977-871f-11f1-984b-6cb31113810e",
    "877-ok": "278fa9aa-8675-11f1-984a-6cb31113810e",
    "884-broken": "9f4cf81a-869a-11f1-984a-6cb31113810e",
}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    rows = {}
    all_keys: set[str] = set()
    for label, ref in CASES.items():
        url = f"{base}{quote(ENTITY)}(guid'{ref}')?$format=json"
        item = httpx.get(url, auth=auth, timeout=60).json()
        rows[label] = item
        all_keys.update(item.keys())

    skip = {"ФайлХранилище_Base64Data", "ТекстХранилище", "ФайлХранилище"}
    keys = sorted(k for k in all_keys if k not in skip and not k.endswith("@navigationLinkUrl"))
    diff = {}
    for key in keys:
        values = {label: rows[label].get(key) for label in CASES}
        uniq = {json.dumps(v, ensure_ascii=False, sort_keys=True) for v in values.values()}
        if len(uniq) > 1:
            diff[key] = values

    print(json.dumps({"diff_fields": diff, "all_labels": list(CASES)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
