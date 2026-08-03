"""Diagnose global attachment regression: 760/762 OData metadata + bytes."""
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
AI_AUTHOR = "a5e55eea-3a0a-11f0-9679-6cb31113810c"
DOC760 = "20dbfa4d-8689-11f1-984a-6cb31113810e"
DOC762 = "18516943-871f-11f1-984b-6cb31113810e"
REF760 = "27997dc5-8689-11f1-984a-6cb31113810e"
SKIP = {"ФайлХранилище_Base64Data", "DataVersion", "Predefined", "PredefinedDataName"}
META = [
    "Ref_Key", "Description", "Расширение", "Размер", "ТипХраненияФайла",
    "ФайлХранилище_Type", "ТекстХранилище_Type", "ТекстХранилище_Base64Data",
    "Автор_Key", "Изменил_Key", "Редактирует_Key", "Том_Key", "ПутьКФайлу",
    "DeletionMark", "ДатаСоздания", "ДатаМодификацииУниверсальная",
    "ХранитьВерсии", "ПодписанЭП", "Зашифрован", "IsFolder", "Parent_Key",
    "ИндексКартинки", "Описание", "СтатусИзвлеченияТекста", "ДатаЗаема",
]


def fetch_all_for_owner(base: str, auth, owner: str) -> list[dict]:
    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = (
        f"{base}{quote(ENTITY)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=Ref_Key desc&$top=20"
    )
    return httpx.get(url, auth=auth, timeout=120).json().get("value", [])


def fetch_by_ref(base: str, auth, ref: str) -> dict:
    url = f"{base}{quote(ENTITY)}(guid'{ref}')?$format=json"
    return httpx.get(url, auth=auth, timeout=120).json()


def summarize(item: dict, content: bytes) -> dict:
    return {
        "ref": item.get("Ref_Key"),
        "desc": item.get("Description"),
        "meta": {k: item.get(k) for k in META},
        "bytes": len(content),
        "magic_ok": content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        "size_match": int(item.get("Размер") or 0) == len(content),
    }


def diff_meta(a: dict, b: dict) -> dict:
    keys = sorted(set(a) | set(b))
    return {k: {"a": a.get(k), "b": b.get(k)} for k in keys if a.get(k) != b.get(k)}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    fm = load_attached_file_field_map()
    entity = fm["entity"]
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )

    items760 = fetch_all_for_owner(base, auth, DOC760)
    items762 = fetch_all_for_owner(base, auth, DOC762)
    ref760 = fetch_by_ref(base, auth, REF760)

    content760 = read_attached_file_storage_bytes(
        client, entity=entity, ref_key=REF760, field_map=fm
    )
    latest762 = items762[0] if items762 else {}
    ref762 = str(latest762.get("Ref_Key") or "")
    content762 = (
        read_attached_file_storage_bytes(
            client, entity=entity, ref_key=ref762, field_map=fm
        )
        if ref762
        else b""
    )

    # Manual uploads: msg in database, author != AI
    flt = "ТипХраненияФайла eq 'ВИнформационнойБазе' and Расширение eq 'msg'"
    url = (
        f"{base}{quote(ENTITY)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=ДатаСоздания desc&$top=80"
    )
    all_msg = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
    manual = [
        i for i in all_msg
        if (i.get("Автор_Key") or "").casefold() != AI_AUTHOR.casefold()
    ][:5]

    manual_samples = []
    for item in manual:
        ref = item.get("Ref_Key", "")
        content = read_attached_file_storage_bytes(
            client, entity=entity, ref_key=ref, field_map=fm
        ) if ref else b""
        manual_samples.append(summarize(item, content))

    full760 = {k: v for k, v in ref760.items() if k not in SKIP}
    full762 = {k: v for k, v in latest762.items() if k not in SKIP}
    report = {
        "760_all_attachments": [
            summarize(i, read_attached_file_storage_bytes(
                client, entity=entity,
                ref_key=str(i.get("Ref_Key")),
                field_map=fm,
            ) if i.get("Ref_Key") else b"")
            for i in items760
        ],
        "762_all_attachments": [
            summarize(i, read_attached_file_storage_bytes(
                client, entity=entity,
                ref_key=str(i.get("Ref_Key")),
                field_map=fm,
            ) if i.get("Ref_Key") else b"")
            for i in items762
        ],
        "760_ref_unchanged": REF760 == str(ref760.get("Ref_Key")),
        "760_vs_762_meta_diff": diff_meta(full760, full762),
        "manual_msg_samples": manual_samples,
        "760_vs_manual_diff": (
            diff_meta(full760, manual[0]) if manual else {}
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
