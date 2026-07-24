"""Compare OData attachments: 000760 ok vs 000762/003884 broken."""
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

CASES = [
    ("000760-ok", "АЛ00-000760", None),
    ("000762-broken", "АЛ00-000762", None),
    ("003884-broken", "НП00-003884", "a54e387c-868c-11f1-984a-6cb31113810e"),
    ("003877-ok", "НП00-003877", "fdb2cd68-8669-11f1-984a-6cb31113810e"),
]
META = [
    "Ref_Key",
    "Description",
    "Расширение",
    "Размер",
    "ТипХраненияФайла",
    "ФайлХранилище_Type",
    "ДатаСоздания",
    "Автор_Key",
    "Редактировал_Key",
    "Том_Key",
    "ПутьКФайлу",
]


def parse_msg(content: bytes, path: Path) -> dict:
    path.write_bytes(content)
    try:
        from aspose.email_foss import msg as msgmod

        m = msgmod.MapiMessage.from_file(str(path))
        att_count = len(getattr(m, "attachments", None) or [])
        atts = []
        for i, a in enumerate(getattr(m, "attachments", None) or []):
            atts.append(
                {
                    "i": i,
                    "name": getattr(a, "display_name", None) or getattr(a, "long_file_name", None),
                    "size": len(getattr(a, "content_stream", None) or b""),
                }
            )
        return {
            "ok": True,
            "subject": getattr(m, "subject", "") or "",
            "attachments": att_count,
            "attachment_details": atts[:10],
            "body_len": len(getattr(m, "body", "") or ""),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def analyze(content: bytes) -> dict:
    size = len(content)
    return {
        "size": size,
        "size_mod_512": size % 512,
        "magic_ok": content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_is_781kb": size == 799744,
        "size_is_800kb_band": 790_000 <= size <= 810_000,
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
        timeout_sec=120,
    )
    out_dir = ROOT / "data" / "temp" / "compare_762"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = []
    contents: dict[str, bytes] = {}
    for label, desc, owner in CASES:
        if owner:
            flt = f"ВладелецФайла_Key eq guid'{owner}'"
            url = (
                f"{base}{quote(entity)}?$format=json"
                f"&$filter={quote(flt)}&$orderby=Ref_Key desc&$top=20"
            )
            items = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
            item = next(
                (i for i in items if (i.get("Description") or "").strip() == desc),
                items[0] if items else {},
            )
        else:
            flt = f"Description eq '{desc}'"
            url = (
                f"{base}{quote(entity)}?$format=json"
                f"&$filter={quote(flt)}&$orderby=Ref_Key desc&$top=5"
            )
            items = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
            item = items[0] if items else {}
        ref = item.get("Ref_Key", "")
        content = (
            read_attached_file_storage_bytes(
                client, entity=entity, ref_key=ref, field_map=fm
            )
            if ref
            else b""
        )
        contents[label] = content
        path = out_dir / f"{label}.msg"
        report.append(
            {
                "label": label,
                "desc": desc,
                "ref": ref,
                "meta": {k: item.get(k) for k in META},
                "ole": analyze(content),
                "aspose": parse_msg(content, path) if content else {},
            }
        )

    if "000760-ok" in contents and "000762-broken" in contents:
        b1, b2 = contents["000760-ok"], contents["000762-broken"]
        report.append(
            {
                "compare_760_vs_762": {
                    "same_bytes": b1 == b2,
                    "size_delta": len(b2) - len(b1),
                    "first_diff": next(
                        (i for i in range(min(len(b1), len(b2))) if b1[i] != b2[i]),
                        None,
                    ),
                }
            }
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
