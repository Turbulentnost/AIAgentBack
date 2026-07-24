"""Verify newest attachment on doc matches working 760 metadata."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
REF_OK = "27997dc5-8689-11f1-984a-6cb31113810e"
REF_NEW = "b664d818-8729-11f1-984b-6cb31113810e"
CHECK = [
    "DeletionMark", "ТипХраненияФайла", "ФайлХранилище_Type", "Том_Key",
    "ПутьКФайлу", "Автор_Key", "Редактирует_Key", "Изменил_Key", "ИндексКартинки",
    "Размер", "Расширение", "Description",
]
EMPTY = "00000000-0000-0000-0000-000000000000"


def fetch(base, auth, ref):
    url = f"{base}{quote(ENTITY)}(guid'{ref}')?$format=json"
    return httpx.get(url, auth=auth, timeout=60).json()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    fm = load_attached_file_field_map()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    ok = fetch(base, auth, REF_OK)
    new = fetch(base, auth, REF_NEW)
    content = read_attached_file_storage_bytes(
        client, entity=ENTITY, ref_key=REF_NEW, field_map=fm
    )
    diff = {
        k: {"760-ok": ok.get(k), "762-new": new.get(k)}
        for k in CHECK
        if ok.get(k) != new.get(k) and k not in {"Description", "Размер"}
    }
    report = {
        "762-new": {k: new.get(k) for k in CHECK},
        "storage_bytes": len(content),
        "cfb_magic": content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        "editor_is_empty": (new.get("Редактирует_Key") or EMPTY) == EMPTY,
        "deletion_mark_false": new.get("DeletionMark") is False,
        "metadata_diff_vs_760": diff,
        "pass": (
            (new.get("Редактирует_Key") or EMPTY) == EMPTY
            and new.get("DeletionMark") is False
            and new.get("ТипХраненияФайла") == ok.get("ТипХраненияФайла")
            and len(content) > 0
            and content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
