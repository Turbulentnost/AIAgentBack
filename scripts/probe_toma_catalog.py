"""Probe OData Catalog_Тома / volume metadata from working attachments."""
from __future__ import annotations

import json
import sys
from urllib.parse import quote

import httpx

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
VOLUME_KEY = "21886495-364e-11ea-82f2-ac1f6b05524c"
WORKING_REFS = {
    "3900-current-db": "e351f21d-89af-11f1-984f-6cb31113810c",
    "3900-old-volume": "0689f586-39f5-11f0-9679-6cb31113810c",
    "manual-outlook": "598a6fa7-8759-11f1-984c-6cb31113810e",
    "760-agent-db": "b63a9c9d-8767-11f1-984c-6cb31113810e",
}
TOM_ENTITIES = (
    "Catalog_Тома",
    "Catalog_ТомаХраненияФайлов",
    "Catalog_ТомаФайлов",
    "InformationRegister_ТомаХраненияФайлов",
)


def probe_entity(base: str, auth, name: str) -> dict:
    url = f"{base}{quote(name)}?$format=json&$top=5"
    try:
        r = httpx.get(url, auth=auth, timeout=60)
        if r.status_code >= 400:
            return {"entity": name, "status": r.status_code, "body": r.text[:400]}
        data = r.json()
        values = data.get("value", [])
        return {"entity": name, "count": len(values), "sample": values[:3]}
    except Exception as exc:
        return {"entity": name, "error": str(exc)}


def probe_volume_by_key(base: str, auth, entity: str, key: str) -> dict:
    url = f"{base}{quote(entity)}(guid'{key}')?$format=json"
    try:
        r = httpx.get(url, auth=auth, timeout=60)
        if r.status_code >= 400:
            return {"entity": entity, "status": r.status_code, "body": r.text[:400]}
        return {"entity": entity, "record": r.json()}
    except Exception as exc:
        return {"entity": entity, "error": str(exc)}


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

    report: dict = {"toma_probes": [], "volume_key_probe": [], "attachments": {}}

    for ent in TOM_ENTITIES:
        report["toma_probes"].append(probe_entity(base, auth, ent))
        report["volume_key_probe"].append(probe_volume_by_key(base, auth, ent, VOLUME_KEY))

    meta_keys = (
        "Ref_Key",
        "Description",
        "Расширение",
        "Размер",
        "ТипХраненияФайла",
        "Том_Key",
        "ПутьКФайлу",
        "ФайлХранилище_Type",
        "Редактирует_Key",
        "Изменил_Key",
        "Автор_Key",
        "DeletionMark",
        "ДатаСоздания",
        "ДатаМодификацииУниверсальная",
        "СтатусИзвлеченияТекста",
    )
    for label, ref in WORKING_REFS.items():
        rec = client.get_by_key(ENTITY, ref) or {}
        stream = client.get_entity_stream(ENTITY, ref, "ФайлХранилище") or b""
        b64 = rec.get("ФайлХранилище_Base64Data") or ""
        report["attachments"][label] = {
            k: rec.get(k) for k in meta_keys if k in rec or rec.get(k) is not None
        }
        report["attachments"][label]["_stream_len"] = len(stream)
        report["attachments"][label]["_b64_present"] = bool(b64)
        report["attachments"][label]["_exists"] = bool(rec)

    out = ROOT / "data" / "temp" / "probe_toma_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
