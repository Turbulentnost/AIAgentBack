"""Upload tiny test file to 2026 АЛ00-000762 — isolate OData vs document issue."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    AttachedFileInput,
    attach_file_to_incoming_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.odata_integration import resolve_attached_file_author_key  # noqa: E402

DOC762 = "18516943-871f-11f1-984b-6cb31113810e"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    fm = load_attached_file_field_map()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    author = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key or "",
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )
    content = b"agent-pochta isolation test 762\n"
    result = attach_file_to_incoming_document(
        client,
        document_ref_key=DOC762,
        file_input=AttachedFileInput(
            filename="agent_probe_762.txt",
            content=content,
            author_key=author or None,
            processed_at=now_attached_file_processed_at(),
        ),
        field_map=fm,
    )
    stored = read_attached_file_storage_bytes(
        client, entity=result.entity, ref_key=result.ref_key, field_map=fm
    )
    print(
        json.dumps(
            {
                "doc_ref": DOC762,
                "ref_key": result.ref_key,
                "stored_eq": stored == content,
                "verify_in_1c": "Open АЛ00-000762 dated 24.07.2026, file agent_probe_762.txt",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
