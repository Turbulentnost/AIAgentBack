"""DELETE all OData attachments for a document by number."""
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
    delete_attached_files_for_document,
    list_attached_files_for_document,
    load_attached_file_field_map,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"


def resolve_doc_ref(base: str, auth, number: str) -> str | None:
    url = (
        f"{base}{quote(DOC_ENTITY)}?$format=json"
        f"&$filter={quote(f'Number eq {chr(39)}{number}{chr(39)}')}"
        f"&$orderby=Date desc&$top=1"
    )
    items = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
    return items[0].get("Ref_Key") if items else None


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    number = (sys.argv[1] if len(sys.argv) > 1 else "АЛ00-000762").strip()
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
    doc_ref = resolve_doc_ref(base, auth, number)
    if not doc_ref:
        print(json.dumps({"error": f"document {number} not found"}, ensure_ascii=False))
        raise SystemExit(1)

    before = list_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )
    deleted = delete_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )
    after = list_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )
    print(
        json.dumps(
            {
                "document": number,
                "doc_ref": doc_ref,
                "before_count": len(before),
                "deleted": deleted,
                "after_count": len(after),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
