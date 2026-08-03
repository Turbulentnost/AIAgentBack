"""Delete volume attachments on АЛ00-000762 and re-upload in database mode (760 template)."""
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
    AttachedFileInput,
    attach_files_to_incoming_document,
    delete_attached_files_for_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.odata_integration import resolve_attached_file_author_key  # noqa: E402

DOC762 = "18516943-871f-11f1-984b-6cb31113810e"
SKIP_DIFF = {
    "Ref_Key",
    "Description",
    "Расширение",
    "Размер",
    "ДатаСоздания",
    "ДатаМодификацииУниверсальная",
    "ВладелецФайла_Key",
}
REF_760 = "27997dc5-8689-11f1-984a-6cb31113810e"


def strip(rec: dict) -> dict:
    skip = {"ФайлХранилище_Base64Data", "DataVersion", "odata.metadata"}
    return {
        k: v
        for k, v in rec.items()
        if k not in skip and not k.endswith("@navigationLinkUrl")
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    fm = load_attached_file_field_map()
    entity = fm["entity"]
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    author = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key or "",
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )

    existing = []
    flt = f"ВладелецФайла_Key eq guid'{DOC762}'"
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}"
    for item in httpx.get(url, auth=auth, timeout=120).json().get("value", []):
        ref = str(item.get("Ref_Key") or "").strip()
        if not ref:
            continue
        ext = (item.get("Расширение") or "").strip()
        desc = (item.get("Description") or "").strip()
        filename = f"{desc}.{ext}" if ext else desc
        content = read_attached_file_storage_bytes(
            client, entity=entity, ref_key=ref, field_map=fm
        )
        existing.append({"ref": ref, "filename": filename, "content": content})

    deleted = delete_attached_files_for_document(
        client, document_ref_key=DOC762, field_map=fm
    )

    processed_at = now_attached_file_processed_at()
    files = [
        AttachedFileInput(
            filename=item["filename"],
            content=item["content"],
            author_key=author or None,
            processed_at=processed_at,
        )
        for item in existing
        if item["content"]
    ]
    if not files:
        raise SystemExit("No attachment bytes to re-upload")

    results = attach_files_to_incoming_document(
        client,
        document_ref_key=DOC762,
        files=files,
        field_map=fm,
    )

    ref760 = strip(
        httpx.get(
            f"{base}{quote(entity)}(guid'{REF_760}')?$format=json",
            auth=auth,
            timeout=120,
        ).json()
    )
    uploaded = []
    for result in results:
        rec = strip(client.get_by_key(entity, result.ref_key) or {})
        diff = {
            k: {"760": ref760.get(k), "762": rec.get(k)}
            for k in sorted(set(ref760) | set(rec))
            if ref760.get(k) != rec.get(k) and k not in SKIP_DIFF
        }
        uploaded.append(
            {
                "filename": f"{result.filename}.{result.extension}",
                "ref_key": result.ref_key,
                "size_bytes": result.size_bytes,
                "diff_vs_760": diff,
            }
        )

    print(
        json.dumps(
            {
                "deleted_refs": deleted,
                "uploaded": uploaded,
                "verify_in_1c": (
                    "Open АЛ00-000762 dated 24.07.2026, open MSG and PDF attachments"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
