"""Compare composite ref Type fields on attachments."""
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
    "760-ok": "27997dc5-8689-11f1-984a-6cb31113810e",
    "762-cur": "ba6972cd-8727-11f1-984b-6cb31113810e",
    "877-ok": "278fa9aa-8675-11f1-984a-6cb31113810e",
    "884-broken": "9f4cf81a-869a-11f1-984a-6cb31113810e",
}
KEYS = [
    "Ref_Key",
    "Description",
    "ВладелецФайла",
    "ВладелецФайла_Type",
    "ВладелецФайла_Key",
    "Автор",
    "Автор_Type",
    "Автор_Key",
    "Редактирует",
    "Редактирует_Type",
    "Редактирует_Key",
    "Изменил_Key",
    "Том_Key",
    "ТипХраненияФайла",
]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    out: dict = {}
    for label, ref in REFS.items():
        url = f"{base}{quote(ENTITY)}(guid'{ref}')?$format=json"
        item = httpx.get(url, auth=auth, timeout=60).json()
        out[label] = {k: item.get(k) for k in KEYS}
        out[label]["_extra_type_fields"] = {
            k: item.get(k)
            for k in sorted(item.keys())
            if k.endswith("_Type") and k not in KEYS
        }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
