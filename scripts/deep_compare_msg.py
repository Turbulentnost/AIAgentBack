"""Deep byte/metadata compare: working vs broken .msg attachments."""
from __future__ import annotations

import hashlib
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

# (label, owner_ref, description or None for all)
CASES = [
    ("003884-new", "a54e387c-868c-11f1-984a-6cb31113810e", "НП00-003884", "9f4cf81a-869a-11f1-984a-6cb31113810e"),
    ("003884-old", "a54e387c-868c-11f1-984a-6cb31113810e", "НП00-003884", "abf67d81-868c-11f1-984a-6cb31113810e"),
    ("003877-ok", "fdb2cd68-8669-11f1-984a-6cb31113810e", "НП00-003877", "278fa9aa-8675-11f1-984a-6cb31113810e"),
    ("000760-ok", None, "АЛ00-000760", None),
]

META_KEYS = [
    "Ref_Key",
    "Description",
    "Расширение",
    "Размер",
    "ТипХраненияФайла",
    "ФайлХранилище_Type",
    "ДатаСоздания",
    "ДатаМодификацииУниверсальная",
    "Автор_Key",
    "Редактировал_Key",
    "Том_Key",
    "ПутьКФайлу",
    "ИндексКартинки",
    "СтатусИзвлеченияТекста",
    "ТипХраненияФайла",
    "ХранитьВерсии",
    "Редактирует_Key",
    "ПодписанЭП",
    "Зашифрован",
]


def fetch_by_ref(base, auth, entity, ref):
    url = f"{base}{quote(entity)}(guid'{ref}')?$format=json"
    with httpx.Client(timeout=120, auth=auth) as hc:
        return hc.get(url).raise_for_status().json()


def find_by_description(base, auth, entity, desc):
    flt = f"Description eq '{desc}'"
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$orderby=Ref_Key desc&$top=5"
    with httpx.Client(timeout=120, auth=auth) as hc:
        items = hc.get(url).raise_for_status().json().get("value", [])
    return items[0] if items else {}


def parse_msg(content: bytes, path: Path) -> dict:
    path.write_bytes(content)
    try:
        from aspose.email_foss import msg as msgmod

        m = msgmod.MapiMessage.from_file(str(path))
        sender = getattr(m, "sender_email_address", None) or getattr(m, "sender_name", None)
        if sender is None and getattr(m, "sender", None) is not None:
            sender = str(m.sender)
        att_count = len(getattr(m, "attachments", None) or [])
        body = (getattr(m, "body", None) or "")[:200]
        return {
            "ok": True,
            "subject": getattr(m, "subject", "") or "",
            "from": str(sender or ""),
            "attachments": att_count,
            "body_preview": body.replace("\r", " ").replace("\n", " ")[:120],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def analyze_ole(content: bytes) -> dict:
    size = len(content)
    magic_ok = content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    # OLE header sector size at offset 0x1E (2 bytes, usually 9 = 512)
    sector_pow = int.from_bytes(content[30:32], "little") if size >= 32 else 0
    sector_size = 512 if sector_pow == 9 else (4096 if sector_pow == 12 else None)
    return {
        "size": size,
        "size_mod_512": size % 512,
        "size_mod_4096": size % 4096,
        "size_is_1mb": size == 1048576,
        "size_is_781kb": size == 799744,
        "magic_ok": magic_ok,
        "sector_pow": sector_pow,
        "sector_size": sector_size,
        "sha256": hashlib.sha256(content).hexdigest(),
        "tail16": content[-16:].hex() if size >= 16 else "",
    }


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
        timeout_sec=settings.odata_timeout_sec,
    )
    out_dir = ROOT / "data" / "temp" / "deep_compare"
    out_dir.mkdir(parents=True, exist_ok=True)

    report = []
    contents: dict[str, bytes] = {}

    for label, owner, desc, ref in CASES:
        if ref:
            item = fetch_by_ref(base, auth, entity, ref)
        else:
            item = find_by_description(base, auth, entity, desc)
            ref = item.get("Ref_Key", "")

        content = read_attached_file_storage_bytes(
            client, entity=entity, ref_key=ref, field_map=fm
        )
        contents[label] = content
        path = out_dir / f"{label}.msg"
        meta = {k: item.get(k) for k in META_KEYS if k in item or item.get(k) is not None}
        # include all extra keys from OData
        extra_keys = sorted(set(item.keys()) - set(META_KEYS))
        meta["_extra_fields"] = {k: item.get(k) for k in extra_keys[:30]}

        b64 = item.get("ФайлХранилище_Base64Data") or ""
        report.append(
            {
                "label": label,
                "ref": ref,
                "meta": meta,
                "b64_present": bool(b64),
                "b64_len": len(b64),
                "ole": analyze_ole(content),
                "aspose": parse_msg(content, path),
                "saved": str(path),
            }
        )

    # byte compare 003884 old vs new
    if "003884-new" in contents and "003884-old" in contents:
        same = contents["003884-new"] == contents["003884-old"]
        report.append({"compare_003884_old_new_identical": same})

    if "003884-new" in contents and "003877-ok" in contents:
        b1, b2 = contents["003884-new"], contents["003877-ok"]
        diff_at = next((i for i in range(min(len(b1), len(b2))) if b1[i] != b2[i]), None)
        report.append(
            {
                "compare_003884_vs_003877": {
                    "size_delta": len(b1) - len(b2),
                    "first_diff_offset": diff_at,
                }
            }
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
