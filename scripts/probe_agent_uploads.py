"""Find working agent-uploaded PDF/MSG attachments and compare metadata."""
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
AUTHOR = "a5e55eea-3a0a-11f0-9679-6cb31113810c"
FIELDS = [
    "Ref_Key", "Description", "Расширение", "Размер", "ТипХраненияФайла",
    "ФайлХранилище_Type", "ИндексКартинки", "ДатаСоздания", "ВладелецФайла_Key",
    "Редактирует_Key", "Автор_Key", "DeletionMark",
]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    flt = f"Автор_Key eq guid'{AUTHOR}' and ТипХраненияФайла eq 'ВИнформационнойБазе'"
    url = (
        f"{base}{quote(ENTITY)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=ДатаСоздания desc&$top=30"
    )
    items = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
    by_ext: dict[str, list] = {"msg": [], "pdf": [], "other": []}
    for item in items:
        ext = (item.get("Расширение") or "").lower()
        row = {k: item.get(k) for k in FIELDS}
        bucket = ext if ext in by_ext else "other"
        by_ext[bucket].append(row)
    print(json.dumps(by_ext, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
