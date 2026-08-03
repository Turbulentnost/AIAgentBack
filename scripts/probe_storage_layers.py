"""Compare OData stream vs Base64 vs document metadata for attachment refs."""
from __future__ import annotations

import base64
import json
import sys
from urllib.parse import quote

import httpx

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
REFS = {
    "760-ok": "27997dc5-8689-11f1-984a-6cb31113810e",
    "762-cur": "b664d818-8729-11f1-984b-6cb31113810e",
    "877-ok": "278fa9aa-8675-11f1-984a-6cb31113810e",
    "884-broken": "9f4cf81a-869a-11f1-984a-6cb31113810e",
}
OWNERS = {
    "760-doc": "20dbfa4d-8689-11f1-984a-6cb31113810e",
    "762-doc": "18516943-871f-11f1-984b-6cb31113810e",
}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    report: dict = {"attachments": {}, "documents": {}}

    for label, ref in REFS.items():
        url = f"{base}{quote(ENTITY)}(guid'{ref}')?$format=json"
        rec = httpx.get(url, auth=auth, timeout=120).json()
        b64 = rec.get("ФайлХранилище_Base64Data") or ""
        stream = client.get_entity_stream(ENTITY, ref, "ФайлХранилище")
        decoded = base64.b64decode(b64) if b64 else b""
        report["attachments"][label] = {
            "Ref_Key": ref,
            "Размер": rec.get("Размер"),
            "ТипХраненияФайла": rec.get("ТипХраненияФайла"),
            "ФайлХранилище_Type": rec.get("ФайлХранилище_Type"),
            "b64_present": bool(b64),
            "b64_len": len(b64),
            "decoded_len": len(decoded),
            "stream_len": len(stream),
            "stream_eq_decoded": stream == decoded,
            "cfb_stream": stream[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
            "DeletionMark": rec.get("DeletionMark"),
            "СтатусИзвлеченияТекста": rec.get("СтатусИзвлеченияТекста"),
            "ИндексКартинки": rec.get("ИндексКартинки"),
            "Редактирует_Key": rec.get("Редактирует_Key"),
            "ВладелецФайла_Key": rec.get("ВладелецФайла_Key"),
        }

    for label, ref in OWNERS.items():
        url = f"{base}{quote(DOC_ENTITY)}(guid'{ref}')?$format=json"
        rec = httpx.get(url, auth=auth, timeout=120).json()
        report["documents"][label] = {
            k: rec.get(k)
            for k in (
                "Ref_Key",
                "Number",
                "Date",
                "Posted",
                "DeletionMark",
                "ПометкаУдаления",
                "Статус",
            )
        }

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
