"""Probe OData metadata and attachments for editor-related fields."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
EDITOR_NEEDLES = ("Редактировал", "Редактирует", "Отредактировал", "Автор")


def metadata_hits(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for needle in EDITOR_NEEDLES:
        hits: list[str] = []
        for m in re.finditer(re.escape(needle), text):
            start = max(0, m.start() - 100)
            end = min(len(text), m.end() + 100)
            snippet = text[start:end].replace("\n", " ")
            hits.append(snippet)
            if len(hits) >= 8:
                break
        out[needle] = hits
    return out


def sample_attachments_with_editor(base: str, auth: tuple[str, str]) -> list[dict]:
    """Find attachments where any editor-like field is non-empty."""
    url = (
        f"{base}{quote(ENTITY)}?$format=json"
        f"&$orderby=Ref_Key desc&$top=200"
    )
    items = httpx.get(url, auth=auth, timeout=120).raise_for_status().json().get("value", [])
    rows = []
    for item in items:
        editor_keys = {
            k: v
            for k, v in item.items()
            if any(n in k for n in EDITOR_NEEDLES) and v not in (None, "", "00000000-0000-0000-0000-000000000000")
        }
        if editor_keys:
            rows.append(
                {
                    "Ref_Key": item.get("Ref_Key"),
                    "Description": item.get("Description"),
                    "editor_keys": editor_keys,
                }
            )
    return rows


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)

    meta_url = f"{base}$metadata"
    meta_text = httpx.get(meta_url, auth=auth, timeout=120).raise_for_status().text
    entity_block = ""
    marker = f'EntityType Name="{ENTITY}"'
    idx = meta_text.find(marker)
    if idx >= 0:
        end = meta_text.find("</EntityType>", idx)
        entity_block = meta_text[idx:end + len("</EntityType>")] if end >= 0 else meta_text[idx:idx + 8000]

    report = {
        "metadata_entity_properties": re.findall(
            r'Property Name="([^"]*(?:Редактир|Отредактир|Автор)[^"]*)"',
            entity_block,
        ),
        "metadata_hits": metadata_hits(entity_block or meta_text),
        "samples_with_editor": sample_attachments_with_editor(base, auth),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
