"""Metadata-only diff: working 760 vs broken 762 attachments."""
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
REFS = {
    "760-msg": "27997dc5-8689-11f1-984a-6cb31113810e",
    "762-msg": "0706c838-872f-11f1-984b-6cb31113810e",
    "762-pdf": "0706c857-872f-11f1-984b-6cb31113810e",
}
SKIP = {"ФайлХранилище_Base64Data", "DataVersion", "odata.metadata"}


def fetch(base: str, auth, ref: str) -> dict:
    url = f"{base}{quote(ENTITY)}(guid'{ref}')?$format=json"
    rec = httpx.get(url, auth=auth, timeout=120).json()
    return {k: v for k, v in rec.items() if k not in SKIP}


def diff(a: dict, b: dict) -> dict:
    keys = sorted(set(a) | set(b))
    return {k: {"760": a.get(k), "762": b.get(k)} for k in keys if a.get(k) != b.get(k)}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    records = {label: fetch(base, auth, ref) for label, ref in REFS.items()}
    report = {
        "records": records,
        "msg_diff_760_vs_762": diff(records["760-msg"], records["762-msg"]),
        "pdf_vs_760_msg": diff(records["760-msg"], records["762-pdf"]),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
