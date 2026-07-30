"""Compare broken vs working attachment OData meta."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings
from agent_pochta.services.odata_client import ODataClient

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
BROKEN = "10e07d21-8a80-11f1-9850-6cb31113810e"
DOC = "2b55a128-8a44-11f1-9850-6cb31113810e"
KEYS = (
    "Ref_Key",
    "Description",
    "ТипХраненияФайла",
    "Том_Key",
    "ПутьКФайлу",
    "ФайлХранилище_Type",
    "Размер",
    "Автор_Key",
    "Изменил_Key",
    "Редактирует_Key",
    "ДатаСоздания",
    "DeletionMark",
    "ВладелецФайла_Key",
    "Расширение",
)


def slim(meta: dict) -> dict:
    out = {k: meta.get(k) for k in KEYS}
    b64 = meta.get("ФайлХранилище_Base64Data") or ""
    out["b64_len"] = len(b64) if isinstance(b64, str) else 0
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    s = get_settings()
    c = ODataClient(
        s.odata_base_url,
        username=s.odata_username,
        password=s.odata_password,
        timeout_sec=120,
    )
    print("methods", [m for m in dir(c) if not m.startswith("_")])

    broken = c.get_by_key(ENTITY, BROKEN) or {}
    print("=== BROKEN", BROKEN)
    print(json.dumps(slim(broken), ensure_ascii=False, indent=2))
    try:
        stream = c.get_entity_stream(ENTITY, BROKEN, "ФайлХранилище") or b""
        print("stream_len", len(stream))
    except Exception as exc:
        print("stream_err", str(exc)[:300])

    # working volume samples
    value = c.fetch_filtered(
        ENTITY,
        filter_expr="ТипХраненияФайла eq 'ВТомахНаДиске' and ПутьКФайлу ne ''",
        page_size=5,
    )
    print("=== volume with path", len(value or []))
    for r in (value or [])[:5]:
        print(json.dumps(slim(r), ensure_ascii=False))

    value_ib = c.fetch_filtered(
        ENTITY,
        filter_expr="ТипХраненияФайла eq 'ВИнформационнойБазе'",
        page_size=5,
    )
    print("=== recent IB")
    for r in (value_ib or [])[:5]:
        print(json.dumps(slim(r), ensure_ascii=False))

    rows_doc = c.fetch_filtered(
        ENTITY,
        filter_expr=f"ВладелецФайла_Key eq guid'{DOC}'",
        page_size=30,
    )
    print("=== doc 003921")
    for r in sorted(
        rows_doc or [],
        key=lambda x: str(x.get("ДатаСоздания") or ""),
        reverse=True,
    ):
        print(json.dumps(slim(r), ensure_ascii=False))


if __name__ == "__main__":
    main()
